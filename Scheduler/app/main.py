from fastapi import FastAPI
from app.db.database import Base, engine

# Import routers
from app.api.jobs_route import router as jobs_router
from app.api.worker_route import router as workers_router  # include worker registration

# Create FastAPI app
app = FastAPI(
    title="GPU Scheduler",
    description="Scheduler API for registering workers and submitting jobs",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# Create all tables (for development; in production use Alembic migrations)
Base.metadata.create_all(bind=engine)

# Include API routers
app.include_router(jobs_router, prefix="/jobs", tags=["jobs"])
app.include_router(workers_router, prefix="/workers", tags=["workers"])