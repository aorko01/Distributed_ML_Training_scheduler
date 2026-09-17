import asyncio
import json
import json5
import random
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select

from .headscale_client import ControlUnavailable
from .models import Enrollment, Endpoint, Grant, Session


def verify_policy(actual, required_file):
    policy = json5.loads(actual) if isinstance(actual, str) else actual
    required = json.loads(Path(required_file).read_text())
    if any(not policy.get("tagOwners", {}).get(tag) for tag in ("tag:interactive-gateway", "tag:interactive-endpoint")):
        raise ControlUnavailable("required tag ownership absent")
    for rule in required.get("grants", []):
        if rule not in policy.get("grants", []):
            raise ControlUnavailable("required interactive network grant absent")
    # Other rules may exist for existing devices, but none may broaden this
    # controlled role boundary. Ambiguous wildcard policies fail closed.
    tags = {"tag:interactive-gateway", "tag:interactive-endpoint", "*"}
    for kind in ("grants", "acls"):
        for rule in policy.get(kind, []):
            values = set(rule.get("src", [])) | {v.rsplit(":", 1)[0] if v.startswith("tag:") and v.count(":") > 1 else v for v in rule.get("dst", [])}
            if (tags & values or any(v.startswith("autogroup:") for v in values)) and rule not in required.get(kind, []):
                raise ControlUnavailable("interactive network policy is too broad")


class Reconciler:
    def __init__(self, enrollments, endpoints):
        self.enrollments, self.endpoints = enrollments, endpoints
        self.task = None

    async def once(self):
        service = self.enrollments
        now, cleanup = service.clock(), []
        try:
            nodes = await service.headscale.nodes()
        except ControlUnavailable:
            nodes = None
        with service.database.transaction() as db:
            for enrollment in db.scalars(select(Enrollment)):
                if enrollment.state == "PENDING":
                    # Persisted reservation after crash cannot be safely retried.
                    # The live creator owns it until TTL; mark ambiguous at expiry.
                    if enrollment.expires <= now:
                        enrollment.state = "UNKNOWN_RESULT"
                if enrollment.state in ("KEY_ISSUED", "UNKNOWN_RESULT", "PENDING") and enrollment.expires <= now:
                    enrollment.state, enrollment.ciphertext = "EXPIRED", None
                if enrollment.state in ("REVOKING", "REVOKED", "EXPIRED"):
                    enrollment.ciphertext, enrollment.online = None, False
                    if nodes is not None and enrollment.retry_after <= now:
                        exact = [node["id"] for node in nodes if node["key_id"] == enrollment.key_id]
                        if enrollment.node_id and enrollment.node_id not in exact:
                            exact.append(enrollment.node_id)
                        cleanup.append((enrollment.id, enrollment.key_id, exact))
                elif nodes is not None and enrollment.state in ("KEY_ISSUED", "CONFIRMED"):
                    try:
                        service.observe(db, enrollment, nodes)
                    except HTTPException:
                        enrollment.online = False
            for endpoint in db.scalars(select(Endpoint)):
                if endpoint.state in ("REVOKING", "REVOKED"):
                    continue
                enrollment = db.get(Enrollment, endpoint.enrollment_id)
                if endpoint.lease_expires <= now:
                    service.deny(db, enrollment)
                elif (not self.endpoints.membership(enrollment) or not endpoint.probed
                      or endpoint.probed + service.settings.observation_ttl <= now):
                    endpoint.state = "OFFLINE" if endpoint.probed else "REGISTERED"
            for session in db.scalars(select(Session).where(Session.state == "ACTIVE")):
                if session.lease_expires <= now or session.deadline <= now:
                    session.state = "REVOKED"
        for enrollment_id, key_id, node_ids in cleanup:
            try:
                await service.headscale.cleanup(key_id, node_ids)
                success = True
            except ControlUnavailable:
                success = False
            with service.database.transaction() as db:
                enrollment = db.get(Enrollment, enrollment_id)
                if success:
                    if enrollment.state == "REVOKING":
                        enrollment.state = "REVOKED"
                    enrollment.retry_after = now + service.settings.reconcile_interval
                    for endpoint in db.scalars(select(Endpoint).where(Endpoint.enrollment_id == enrollment_id, Endpoint.state == "REVOKING")):
                        endpoint.state = "REVOKED"
                else:
                    enrollment.retries += 1
                    enrollment.retry_after = now + min(2**min(enrollment.retries, 5), 30) + random.random()

    async def run(self):
        while True:
            try:
                await self.once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # No request/exception repr: upstream payloads may contain keys.
                import logging
                logging.getLogger(__name__).error("reconciliation failed")
            await asyncio.sleep(self.enrollments.settings.reconcile_interval)
