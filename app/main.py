from fastapi import FastAPI
from .database import Base, engine
from .routes import jobs

app = FastAPI()

Base.metadata.create_all(bind=engine)

app.include_router(jobs.router)

print("Application started successfully.")