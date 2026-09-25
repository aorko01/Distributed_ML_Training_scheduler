import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.db.database import Base, engine, legacy_tables, run_migrations

from app.models import interactive_runtime_model
from app.api.worker_execution_route import router as execution_router
from app.api.interactive_runtime_route import router as runtime_router
from app.services import interactive_controller
from app.api.jobs_route import router as jobs_router
from app.api.scheduler_route import router as scheduler_router
from app.api.worker_route import router as workers_router
from app.api.auth_route import router as auth_router
from app.api.admin_workers_route import router as admin_workers_router
from app.api.admin_users_route import router as admin_users_router
from app.api.admin_jobs_route import router as admin_jobs_router
from app.models import worker_credential_model  # noqa: F401 - register table
from app.api.docker_route import router as docker_router
from app.api.resource_route import router as resources_router
from app.services import watchdog_service
from app.api.interactive_workspace_route import router as interactive_router, internal_router as interactive_builder_router
from app.api.interactive_capacity_route import router as interactive_capacity_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    controller = asyncio.create_task(interactive_controller.run())
    watcher = asyncio.create_task(watchdog_service.run_stall_watcher())
    try:
        yield
    finally:
        controller.cancel()
        watcher.cancel()
        try:
            await controller
        except asyncio.CancelledError:
            pass
        try:
            await watcher
        except asyncio.CancelledError:
            pass

app = FastAPI(
    title="GPU Scheduler",
    description="Scheduler API for registering workers and submitting jobs",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

@app.middleware('http')
async def bound_runtime_bodies(request, call_next):
    from fastapi.responses import JSONResponse
    path = request.url.path
    limit = (65536 if path.startswith('/internal/workers/v1/') else
             2048 if path.startswith('/interactive/runtimes/') or path.endswith('/runtimes') else None)
    if limit:
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > limit:
                return JSONResponse({'detail':'Request too large'}, status_code=413)
        request._body = bytes(body)
    return await call_next(request)

# CORS: allow all origins for now
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create all tables (for development; in production use Alembic migrations)
Base.metadata.create_all(bind=engine, tables=legacy_tables())
run_migrations()
# SQLite is used for model-based test/development schema creation only.
if engine.dialect.name != "postgresql":
    Base.metadata.create_all(bind=engine)

app.include_router(jobs_router, prefix="/jobs", tags=["jobs"])
app.include_router(scheduler_router, prefix="/scheduler", tags=["scheduler"])
app.include_router(workers_router, prefix="/workers", tags=["workers"])
app.include_router(auth_router, tags=["auth"])
app.include_router(admin_workers_router)
app.include_router(admin_users_router)
app.include_router(admin_jobs_router)
app.include_router(docker_router, prefix="/docker", tags=["docker"])
app.include_router(resources_router, prefix="/resources", tags=["resources"])
app.include_router(interactive_router)
app.include_router(interactive_builder_router)
app.include_router(interactive_capacity_router)

app.include_router(execution_router)
app.include_router(runtime_router)
