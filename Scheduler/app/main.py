from fastapi import FastAPI
from app.db.database import Base, engine

# Import routers
from app.api.jobs_route import router as jobs_router
from app.api.scheduler_route import router as scheduler_router
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
app.include_router(scheduler_router, prefix="/scheduler", tags=["scheduler"])
app.include_router(workers_router, prefix="/workers", tags=["workers"])

# =============================================================================
# Frontend connectivity — append-only section; original lines above untouched.
# =============================================================================

# CORS: allows the Vite dev server (port 5173) to call this API.
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dashboard route: exposes GET /dashboard for the frontend.
from app.api.dashboard_route import router as dashboard_router

app.include_router(dashboard_router)