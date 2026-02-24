from fastapi import FastAPI
from app.db.database import Base, engine
from app.api.jobs_route import router as jobs_router

app = FastAPI()

Base.metadata.create_all(bind=engine)

app.include_router(jobs_router)
