from fastapi import HTTPException
from sqlalchemy import select

from .auth import require
from .enrollment_service import get, identifier, utc
from .models import Endpoint, Enrollment


class EndpointService:
    def __init__(self, enrollments):
        self.enrollments = enrollments
        self.settings, self.database, self.clock = enrollments.settings, enrollments.database, enrollments.clock

    def membership(self, enrollment):
        return (enrollment.state == "CONFIRMED" and enrollment.online and enrollment.observed is not None
                and enrollment.observed + self.settings.observation_ttl > self.clock())

    def gateway(self, db):
        enrollment = db.scalar(select(Enrollment).where(Enrollment.role == "gateway",
            Enrollment.identity == self.settings.gateway_id, Enrollment.generation == self.settings.gateway_generation))
        if enrollment is None or not self.membership(enrollment):
            raise HTTPException(503, "gateway unavailable")
        return enrollment

    def available(self, db, endpoint, ready=True):
        if endpoint.state in ("REVOKING", "REVOKED"):
            raise HTTPException(410, "revoked")
        enrollment = get(db, Enrollment, endpoint.enrollment_id)
        if endpoint.lease_expires <= self.clock() or not self.membership(enrollment):
            raise HTTPException(404, "unavailable")
        if ready and (endpoint.state != "READY" or not endpoint.probed
                     or endpoint.probed + self.settings.observation_ttl <= self.clock()):
            raise HTTPException(404, "unavailable")
        gateway = self.gateway(db)
        self.enrollments.validate_ips(enrollment.ips, gateway.ips)
        return enrollment

    def generation(self, endpoint, generation):
        if endpoint.generation != generation:
            raise HTTPException(409, "stale generation")

    def register(self, resource_id, request, actor):
        require(actor, "controller")
        with self.database.transaction() as db:
            enrollment = get(db, Enrollment, request.enrollment_id)
            if enrollment.role != "endpoint" or enrollment.identity != resource_id:
                raise HTTPException(422, "invalid enrollment binding")
            if enrollment.generation != request.generation:
                raise HTTPException(409, "stale generation")
            # Fence registrations against every newer enrollment, including one
            # whose remote enrollment hasn't finished yet.
            newest = db.scalar(select(Enrollment).where(Enrollment.role == "endpoint", Enrollment.identity == resource_id)
                               .order_by(Enrollment.created.desc(), Enrollment.id.desc()))
            if newest.id != enrollment.id:
                raise HTTPException(409, "stale generation")
            if not self.membership(enrollment) or request.port not in self.settings.ports:
                raise HTTPException(422, "invalid endpoint")
            endpoint = db.get(Endpoint, resource_id)
            if endpoint and endpoint.owner != request.owner:
                raise HTTPException(403, "owner is immutable")
            if endpoint and endpoint.enrollment_id == enrollment.id:
                if (endpoint.service, endpoint.port, endpoint.protocol) != (request.service, request.port, request.protocol):
                    raise HTTPException(409, "endpoint version is immutable")
                return self.describe(endpoint)
            if endpoint:
                self.enrollments.invalidate(db, resource_id)
            else:
                endpoint = Endpoint(id=resource_id, owner=request.owner)
                db.add(endpoint)
            endpoint.generation, endpoint.enrollment_id = request.generation, enrollment.id
            endpoint.service, endpoint.protocol, endpoint.port = request.service, request.protocol, request.port
            endpoint.version, endpoint.lease_expires = identifier(), self.clock() + self.settings.resource_lease
            endpoint.state, endpoint.probed, endpoint.probe_id = "REGISTERED", None, None
            return self.describe(endpoint)

    def describe(self, endpoint):
        return {"resource_id": endpoint.id, "generation": endpoint.generation, "version": endpoint.version,
                "state": endpoint.state, "lease_expires_at": utc(endpoint.lease_expires)}

    def lease(self, resource_id, generation, actor):
        require(actor, "controller")
        with self.database.transaction() as db:
            endpoint = get(db, Endpoint, resource_id)
            self.generation(endpoint, generation)
            if endpoint.state in ("REVOKING", "REVOKED") or endpoint.lease_expires <= self.clock():
                raise HTTPException(410, "revoked or expired")
            endpoint.lease_expires = self.clock() + self.settings.resource_lease
            return self.describe(endpoint)

    def targets(self, gateway_id, actor):
        require(actor, "gateway")
        if gateway_id != self.settings.gateway_id:
            raise HTTPException(403, "denied")
        targets = []
        with self.database.transaction() as db:
            self.gateway(db)
            for endpoint in db.scalars(select(Endpoint)):
                try:
                    enrollment = self.available(db, endpoint, ready=False)
                except HTTPException:
                    continue
                # A probe assignment remains stable until answered/expired.
                if not endpoint.probe_id or endpoint.probe_expires <= self.clock():
                    endpoint.probe_id, endpoint.probe_expires = identifier(), self.clock() + self.settings.observation_ttl
                    endpoint.probe_gateway = gateway_id
                targets.append({"resource_id": endpoint.id, "generation": endpoint.generation,
                    "version": endpoint.version, "probe_id": endpoint.probe_id, "ips": enrollment.ips, "port": endpoint.port})
        return targets

    def probe(self, resource_id, request, actor):
        require(actor, "gateway")
        with self.database.transaction() as db:
            endpoint = get(db, Endpoint, resource_id)
            self.generation(endpoint, request.generation)
            self.available(db, endpoint, ready=False)
            if (request.version != endpoint.version or request.probe_id != endpoint.probe_id
                    or endpoint.probe_gateway != self.settings.gateway_id or endpoint.probe_expires <= self.clock()):
                raise HTTPException(409, "stale probe")
            endpoint.state = "READY" if request.success else "OFFLINE"
            endpoint.probed = self.clock() if request.success else None
            endpoint.probe_id = None
            return self.describe(endpoint)

    def revoke(self, resource_id, actor):
        require(actor, "controller")
        with self.database.transaction() as db:
            endpoint = get(db, Endpoint, resource_id)
            self.enrollments.deny(db, get(db, Enrollment, endpoint.enrollment_id))
            return self.describe(endpoint)
