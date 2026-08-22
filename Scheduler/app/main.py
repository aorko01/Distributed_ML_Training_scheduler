import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.db.database import Base, engine, run_migrations

# Import routers
from app.api.jobs_route import router as jobs_router
from app.api.scheduler_route import router as scheduler_router
from app.api.worker_route import router as workers_router
from app.api.auth_route import router as auth_router
from app.api.docker_route import router as docker_router
from app.api.resource_route import router as resources_router
from app.services import watchdog_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    watcher = asyncio.create_task(watchdog_service.run_stall_watcher())
    try:
        yield
    finally:
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass

# Create FastAPI app
app = FastAPI(
    title="GPU Scheduler",
    description="Scheduler API for registering workers and submitting jobs",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# CORS: allow all origins for now
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create all tables (for development; in production use Alembic migrations)
Base.metadata.create_all(bind=engine)
run_migrations()

# Include API routers
app.include_router(jobs_router, prefix="/jobs", tags=["jobs"])
app.include_router(scheduler_router, prefix="/scheduler", tags=["scheduler"])
app.include_router(workers_router, prefix="/workers", tags=["workers"])
app.include_router(auth_router, tags=["auth"])
app.include_router(docker_router, prefix="/docker", tags=["docker"])
app.include_router(resources_router, prefix="/resources", tags=["resources"])