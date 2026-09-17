from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import time
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import select

from .auth import require
from .headscale_client import ControlUnavailable, UnknownResult
from .models import Enrollment, Endpoint, Grant, Session


def identifier():
    return str(uuid4())


def utc(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def get(db, model, key):
    value = db.get(model, key)
    if value is None:
        raise HTTPException(404, "unavailable")
    return value


class EnrollmentService:
    def __init__(self, settings, database, headscale, clock=time.time):
        self.settings, self.database, self.headscale, self.clock = settings, database, headscale, clock
        self.cipher = Fernet(settings.encryption_key.encode())

    def invalidate(self, db, resource_id):
        for grant in db.scalars(select(Grant).where(Grant.resource_id == resource_id)):
            grant.state = "REVOKED"
            for session in db.scalars(select(Session).where(Session.grant_id == grant.id)):
                session.state = "REVOKED"

    def deny(self, db, enrollment):
        enrollment.state = "REVOKING"
        enrollment.ciphertext = None
        enrollment.online = False
        enrollment.updated = self.clock()
        for endpoint in db.scalars(select(Endpoint).where(Endpoint.enrollment_id == enrollment.id)):
            endpoint.state = "REVOKING"
            self.invalidate(db, endpoint.id)

    def current(self, db, enrollment):
        newest = db.scalar(select(Enrollment).where(Enrollment.role == enrollment.role,
            Enrollment.identity == enrollment.identity).order_by(Enrollment.created.desc()))
        if newest and newest.id != enrollment.id:
            raise HTTPException(409, "stale generation")
        if enrollment.state in ("REVOKING", "REVOKED", "EXPIRED", "FAILED"):
            raise HTTPException(410, "revoked or expired")

    def status(self, db, enrollment, actor):
        require(actor, "controller", "gateway")
        if actor == "gateway" and (enrollment.role != "gateway" or enrollment.identity != self.settings.gateway_id):
            raise HTTPException(403, "denied")
        return {"id": enrollment.id, "state": enrollment.state, "role": enrollment.role,
                "identity": enrollment.identity, "generation": enrollment.generation,
                "expires_at": utc(enrollment.expires), "node_id": enrollment.node_id,
                "ips": enrollment.ips, "online": enrollment.online,
                "observed_at": utc(enrollment.observed) if enrollment.observed else None,
                "headscale_key_id": enrollment.key_id, "tags": enrollment.tags, "ephemeral": enrollment.role == "endpoint"}

    def replay(self, enrollment):
        if enrollment.expires <= self.clock():
            raise HTTPException(410, "expired")
        if enrollment.state != "KEY_ISSUED" or not enrollment.ciphertext:
            raise HTTPException(503 if enrollment.state in ("PENDING", "UNKNOWN_RESULT") else 410, "enrollment unavailable")
        return {"enrollment_id": enrollment.id, "key": self.cipher.decrypt(enrollment.ciphertext.encode()).decode(),
                "expires_at": utc(enrollment.expires), "login_server": self.settings.login_server,
                "tags": enrollment.tags, "hostname": "interactive-" + enrollment.id[:8]}

    async def enroll(self, request, actor, idempotency):
        require(actor, "controller", "bootstrap")
        if not idempotency or len(idempotency) > 128:
            raise HTTPException(422, "bounded Idempotency-Key required")
        if request.role == "gateway" and (request.identity != self.settings.gateway_id or request.generation != self.settings.gateway_generation):
            raise HTTPException(403, "denied")
        if actor == "bootstrap" and request.role != "gateway":
            raise HTTPException(403, "denied")
        digest = hashlib.sha256(json.dumps(request.model_dump(), sort_keys=True).encode()).hexdigest()
        now = self.clock()
        with self.database.transaction() as db:
            previous = db.scalar(select(Enrollment).where(Enrollment.actor == actor, Enrollment.idempotency_key == idempotency))
            if previous:
                if previous.request_hash != digest:
                    raise HTTPException(409, "idempotency conflict")
                return self.replay(previous)
            existing = db.scalar(select(Enrollment).where(Enrollment.role == request.role,
                Enrollment.identity == request.identity, Enrollment.generation == request.generation))
            if existing:
                # A generation is never recycled, even after expiry/revocation.
                raise HTTPException(409, "generation already enrolled; replace with a new generation")
            tags = ["tag:interactive-" + request.role]
            latest = db.scalar(select(Enrollment).where(Enrollment.role == request.role,
                Enrollment.identity == request.identity).order_by(Enrollment.created.desc()))
            created = max(now, latest.created + 0.000001) if latest else now
            enrollment = Enrollment(id=identifier(), actor=actor, idempotency_key=idempotency, request_hash=digest,
                role=request.role, identity=request.identity, generation=request.generation, tags=tags,
                expires=now + self.settings.enrollment_ttl, state="PENDING", created=created, updated=now)
            db.add(enrollment)
            db.flush()
            for old in db.scalars(select(Enrollment).where(Enrollment.role == request.role,
                    Enrollment.identity == request.identity, Enrollment.id != enrollment.id)):
                if old.state not in ("REVOKED", "EXPIRED", "FAILED"):
                    self.deny(db, old)
            enrollment_id, expires = enrollment.id, enrollment.expires
        try:
            key_id, key = await self.headscale.create_key(tags, request.role == "endpoint", utc(expires))
        except ControlUnavailable as error:
            with self.database.transaction() as db:
                enrollment = get(db, Enrollment, enrollment_id)
                if enrollment.state == "PENDING":
                    enrollment.state = "UNKNOWN_RESULT" if isinstance(error, UnknownResult) else "FAILED"
            raise HTTPException(503, "enrollment unavailable") from None
        with self.database.transaction() as db:
            enrollment = get(db, Enrollment, enrollment_id)
            enrollment.key_id = key_id  # retain exact ID even if concurrent revoke won
            enrollment.updated = self.clock()
            if enrollment.state != "PENDING" or self.clock() >= expires:
                self.deny(db, enrollment)
                response = None
            else:
                enrollment.ciphertext = self.cipher.encrypt(key.encode()).decode()
                enrollment.state = "KEY_ISSUED"
                response = self.replay(enrollment)
        if response is None:
            raise HTTPException(410, "revoked or expired")
        return response

    def validate_ips(self, ips, excluded=()):
        networks = [ipaddress.ip_network(value) for value in self.settings.ranges]
        result = []
        excluded = {str(ipaddress.ip_address(value)) for value in excluded}
        for value in ips:
            try:
                ip = ipaddress.ip_address(value)
            except ValueError:
                raise HTTPException(422, "invalid endpoint") from None
            if str(ip) in excluded or ip.is_loopback or ip.is_unspecified or ip.is_multicast or not any(
                    ip.version == network.version and ip in network for network in networks):
                raise HTTPException(422, "invalid endpoint")
            result.append(str(ip))
        if not result:
            raise HTTPException(422, "invalid endpoint")
        return result

    def observe(self, db, enrollment, nodes):
        matches = [node for node in nodes if node["key_id"] == enrollment.key_id]
        if len(matches) != 1:
            enrollment.online = False
            return False
        node = matches[0]
        if (sorted(node["tags"]) != sorted(enrollment.tags) or node.get("ephemeral") != (enrollment.role == "endpoint")
                or node.get("reusable") is not False or (enrollment.node_id and enrollment.node_id != node["id"])):
            enrollment.online = False
            return False
        excluded = []
        if enrollment.role == "endpoint":
            for gateway in db.scalars(select(Enrollment).where(Enrollment.role == "gateway")):
                excluded.extend(gateway.ips)
        ips = self.validate_ips(node["ips"], excluded)
        if not node["online"] or (node["expiry"] and node["expiry"] <= self.clock()):
            enrollment.online = False
            return False
        if enrollment.ips and enrollment.ips != ips:
            # An unexpected address change requires controller reassignment.
            self.deny(db, enrollment)
            return False
        enrollment.node_id, enrollment.ips = node["id"], ips
        enrollment.observed, enrollment.online = self.clock(), True
        enrollment.state, enrollment.ciphertext = "CONFIRMED", None
        return True

    async def confirm(self, enrollment_id, generation, actor):
        with self.database.transaction() as db:
            enrollment = get(db, Enrollment, enrollment_id)
            self.status(db, enrollment, actor)
            if generation != enrollment.generation:
                raise HTTPException(409, "stale generation")
            self.current(db, enrollment)
        try:
            nodes = await self.headscale.nodes()
        except ControlUnavailable:
            raise HTTPException(503, "membership unavailable") from None
        with self.database.transaction() as db:
            enrollment = get(db, Enrollment, enrollment_id)
            self.current(db, enrollment)
            if enrollment.state != "CONFIRMED" and enrollment.expires <= self.clock():
                raise HTTPException(410, "expired")
            if not self.observe(db, enrollment, nodes):
                raise HTTPException(409, "membership not verified")
            return self.status(db, enrollment, actor)
