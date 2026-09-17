import jwt
from cryptography.hazmat.primitives import serialization
from fastapi import HTTPException
from sqlalchemy import select
from uuid import UUID

from .auth import require
from .enrollment_service import get, identifier, utc
from .models import Endpoint, Grant, Session

REQUIRED = ["iss", "aud", "sub", "jti", "iat", "nbf", "exp", "resource_id", "generation", "service", "protocol"]


class GrantService:
    def __init__(self, endpoints):
        self.endpoints = endpoints
        self.settings, self.database, self.clock = endpoints.settings, endpoints.database, endpoints.clock
        self.private = serialization.load_pem_private_key(self.settings.signing_key.encode(), password=None)

    def issue(self, request, actor):
        require(actor, "controller")
        with self.database.transaction() as db:
            endpoint = get(db, Endpoint, request.resource_id)
            if request.user != endpoint.owner or request.gateway_id != self.settings.gateway_id:
                raise HTTPException(403, "denied")
            self.endpoints.generation(endpoint, request.generation)
            if endpoint.service != request.service:
                raise HTTPException(404, "unavailable")
            self.endpoints.available(db, endpoint)
            now, jti = int(self.clock()), identifier()
            expires = now + self.settings.admission_ttl
            ticket = jwt.encode({"iss": self.settings.issuer, "aud": request.gateway_id, "sub": request.user,
                "jti": jti, "iat": now, "nbf": now, "exp": expires, "resource_id": endpoint.id,
                "generation": endpoint.generation, "service": endpoint.service, "protocol": "tcp-stream-v1"},
                self.private, algorithm="EdDSA", headers={"kid": self.settings.signing_kid})
            db.add(Grant(id=jti, user=request.user, resource_id=endpoint.id, generation=endpoint.generation,
                service=endpoint.service, gateway=request.gateway_id, expires=expires,
                deadline=now + self.settings.session_max, state="ISSUED", ticket=ticket))
            return {"grant_id": jti, "ticket": ticket, "expires_at": utc(expires)}

    def decode(self, ticket, admission=True):
        try:
            header = jwt.get_unverified_header(ticket)
            if header.get("alg") != "EdDSA":
                raise ValueError()
            if header.get("kid") == self.settings.signing_kid:
                public = self.private.public_key()
            else:
                record = self.settings.verification_keys[header["kid"]]
                if record["not_after"] <= self.clock():
                    raise ValueError()
                public = record["pem"]
            claims = jwt.decode(ticket, public, algorithms=["EdDSA"],
                issuer=self.settings.issuer, audience=self.settings.gateway_id,
                options={"require": REQUIRED, "verify_exp": False, "verify_nbf": False, "verify_iat": False})
            if claims["protocol"] != "tcp-stream-v1" or claims["iat"] > self.clock() or claims["nbf"] > self.clock():
                raise ValueError()
            if admission and claims["exp"] <= self.clock():
                raise ValueError()
            return claims
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise HTTPException(401, "invalid ticket") from None

    def destination(self, db, session):
        grant = get(db, Grant, session.grant_id)
        endpoint = get(db, Endpoint, grant.resource_id)
        if (session.state != "ACTIVE" or grant.state != "CLAIMED" or session.deadline <= self.clock()
                or session.lease_expires <= self.clock() or endpoint.generation != grant.generation or endpoint.version != session.version):
            raise HTTPException(410, "revoked or expired")
        enrollment = self.endpoints.available(db, endpoint)
        return {"session_id": session.id, "user": grant.user, "resource_id": endpoint.id,
            "generation": endpoint.generation, "version": session.version, "service": endpoint.service,
            "protocol": endpoint.protocol, "ips": enrollment.ips, "port": endpoint.port,
            "gateway_ips": self.endpoints.gateway(db).ips, "lease_expires_at": utc(session.lease_expires),
            "deadline": utc(session.deadline)}

    def claim(self, request, actor):
        require(actor, "gateway")
        try:
            UUID(request.request_id)
        except ValueError:
            raise HTTPException(422, "invalid request UUID") from None
        # Expiry is checked after recognizing a same-request retry, so a lost
        # response may be recovered after admission expiry while its lease lives.
        claims = self.decode(request.ticket, admission=False)
        with self.database.transaction() as db:
            grant = get(db, Grant, claims["jti"])
            if grant.ticket != request.ticket or grant.gateway != self.settings.gateway_id:
                raise HTTPException(401, "invalid ticket")
            existing = db.scalar(select(Session).where(Session.grant_id == grant.id))
            if existing:
                if existing.request_id != request.request_id or existing.gateway != self.settings.gateway_id:
                    raise HTTPException(410, "ticket consumed")
                return self.destination(db, existing)
            if grant.state != "ISSUED" or grant.expires <= self.clock():
                raise HTTPException(410, "revoked or expired")
            reused = db.scalar(select(Session).where(Session.gateway == self.settings.gateway_id, Session.request_id == request.request_id))
            if reused:
                raise HTTPException(409, "request UUID already used")
            endpoint = get(db, Endpoint, grant.resource_id)
            self.endpoints.generation(endpoint, grant.generation)
            self.endpoints.available(db, endpoint)
            session = Session(id=identifier(), grant_id=grant.id, gateway=self.settings.gateway_id,
                request_id=request.request_id, version=endpoint.version, state="ACTIVE", deadline=grant.deadline,
                lease_expires=min(self.clock() + self.settings.session_lease, grant.deadline))
            grant.state = "CLAIMED"
            db.add(session)
            db.flush()
            return self.destination(db, session)

    def renew(self, session_id, actor):
        require(actor, "gateway")
        with self.database.transaction() as db:
            session = get(db, Session, session_id)
            if session.gateway != self.settings.gateway_id:
                raise HTTPException(403, "denied")
            self.destination(db, session)
            session.lease_expires = min(self.clock() + self.settings.session_lease, session.deadline)
            return self.destination(db, session)

    def release(self, session_id, actor):
        require(actor, "gateway")
        with self.database.transaction() as db:
            session = get(db, Session, session_id)
            if session.gateway != self.settings.gateway_id:
                raise HTTPException(403, "denied")
            if session.state == "ACTIVE":
                session.state = "RELEASED"
            return {"state": session.state}

    def revoke(self, grant_id, actor):
        require(actor, "controller")
        with self.database.transaction() as db:
            grant = get(db, Grant, grant_id)
            grant.state = "REVOKED"
            for session in db.scalars(select(Session).where(Session.grant_id == grant_id)):
                session.state = "REVOKED"
            return {"state": grant.state}
