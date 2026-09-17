import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import Depends, FastAPI, Header, HTTPException

from .auth import authenticate, require
from .config import Settings
from .database import Database
from .endpoint_service import EndpointService
from .enrollment_service import EnrollmentService, get
from .grant_service import GrantService
from .headscale_client import HeadscaleClient, ControlUnavailable
from .models import Enrollment
from .reconciliation import Reconciler, verify_policy
from .schemas import Identifier, Enroll, Generation, Register, Probe, Issue, Claim


def create_app(settings=None, headscale=None, database=None, clock=None, background=True):
    @asynccontextmanager
    async def lifespan(app):
        configured = settings or Settings.from_env()
        adapter = headscale or HeadscaleClient(configured)
        storage = database or Database(configured.database_url)
        if not storage.ready():
            raise RuntimeError("run explicit migrations before starting management")
        service = EnrollmentService(configured, storage, adapter, **({"clock": clock} if clock else {}))
        app.state.settings, app.state.enrollments = configured, service
        app.state.endpoints = EndpointService(service)
        app.state.grants = GrantService(app.state.endpoints)
        reconciler = Reconciler(service, app.state.endpoints)
        task = asyncio.create_task(reconciler.run()) if background else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            await adapter.close()
            storage.engine.dispose()

    app = FastAPI(title="Headscale Management internal API", lifespan=lifespan)
    @app.get("/health/live")
    async def live():
        return {"status": "live"}

    @app.get("/health/ready")
    async def ready():
        try:
            service = app.state.enrollments
            if not service.database.ready() or not service.settings.required_policy_file:
                raise ControlUnavailable()
            async with asyncio.timeout(4):
                verify_policy(await service.headscale.policy(), service.settings.required_policy_file)
                await service.headscale.nodes()
            return {"status": "ready"}
        except (ControlUnavailable, OSError, ValueError, KeyError, TimeoutError):
            raise HTTPException(503, "unavailable") from None

    prefix = "/internal/v1"
    @app.post(prefix + "/enrollments")
    async def enroll(body: Enroll, actor=Depends(authenticate), idempotency: str | None = Header(None, alias="Idempotency-Key")):
        return await app.state.enrollments.enroll(body, actor, idempotency)

    @app.get(prefix + "/enrollments/{enrollment_id}")
    async def status(enrollment_id: Identifier, actor=Depends(authenticate)):
        service = app.state.enrollments
        with service.database.transaction() as db:
            return service.status(db, get(db, Enrollment, enrollment_id), actor)

    @app.post(prefix + "/enrollments/{enrollment_id}/confirm")
    async def confirm(enrollment_id: Identifier, body: Generation, actor=Depends(authenticate)):
        return await app.state.enrollments.confirm(enrollment_id, body.generation, actor)

    @app.delete(prefix + "/enrollments/{enrollment_id}")
    async def revoke_enrollment(enrollment_id: Identifier, actor=Depends(authenticate)):
        require(actor, "controller")
        service = app.state.enrollments
        with service.database.transaction() as db:
            value = get(db, Enrollment, enrollment_id)
            service.deny(db, value)
            return service.status(db, value, actor)

    @app.put(prefix + "/resources/{resource_id}/endpoint")
    async def register(resource_id: Identifier, body: Register, actor=Depends(authenticate)):
        return app.state.endpoints.register(resource_id, body, actor)

    @app.post(prefix + "/resources/{resource_id}/lease")
    async def lease(resource_id: Identifier, body: Generation, actor=Depends(authenticate)):
        return app.state.endpoints.lease(resource_id, body.generation, actor)

    @app.get(prefix + "/gateways/{gateway_id}/probe-targets")
    async def targets(gateway_id: Identifier, actor=Depends(authenticate)):
        return app.state.endpoints.targets(gateway_id, actor)

    @app.post(prefix + "/resources/{resource_id}/probe-result")
    async def probe(resource_id: Identifier, body: Probe, actor=Depends(authenticate)):
        require(actor, "gateway")
        if body.success:
            service = app.state.enrollments
            try:
                async with asyncio.timeout(4):
                    verify_policy(await service.headscale.policy(), service.settings.required_policy_file)
                    nodes = await service.headscale.nodes()
            except (ControlUnavailable, TimeoutError, ValueError, OSError):
                raise HTTPException(503, "membership unavailable") from None
            from .models import Endpoint
            with service.database.transaction() as db:
                endpoint = get(db, Endpoint, resource_id)
                app.state.endpoints.generation(endpoint, body.generation)
                if endpoint.version != body.version:
                    raise HTTPException(409, "stale probe")
                enrollment = get(db, Enrollment, endpoint.enrollment_id)
                service.current(db, enrollment)
                if not service.observe(db, enrollment, nodes):
                    raise HTTPException(404, "unavailable")
        return app.state.endpoints.probe(resource_id, body, actor)

    @app.delete(prefix + "/resources/{resource_id}")
    async def revoke_resource(resource_id: Identifier, actor=Depends(authenticate)):
        return app.state.endpoints.revoke(resource_id, actor)

    @app.post(prefix + "/access-grants")
    async def issue(body: Issue, actor=Depends(authenticate)):
        return app.state.grants.issue(body, actor)

    @app.delete(prefix + "/access-grants/{grant_id}")
    async def revoke_grant(grant_id: Identifier, actor=Depends(authenticate)):
        return app.state.grants.revoke(grant_id, actor)

    @app.post(prefix + "/sessions/claim")
    async def claim(body: Claim, actor=Depends(authenticate)):
        return app.state.grants.claim(body, actor)

    @app.post(prefix + "/sessions/{session_id}/renew")
    async def renew(session_id: Identifier, actor=Depends(authenticate)):
        return app.state.grants.renew(session_id, actor)

    @app.post(prefix + "/sessions/{session_id}/release")
    async def release(session_id: Identifier, actor=Depends(authenticate)):
        return app.state.grants.release(session_id, actor)
    return app


app = create_app()
