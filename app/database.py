from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

#The database URL is expected to be set in the environment variable "DATABASE_URL" in the docker compose file. It should be in the format: "postgresql://user:password@host:port/database"
DATABASE_URL = os.getenv("DATABASE_URL")

# Yes, the engine is responsible for managing connections to the database.
engine = create_engine(DATABASE_URL)

# Session → A Unit of Work
# A session is like a workspace or a transaction scope.
# You don’t need to create a new session for every query, but you often create a session per logical operation (e.g., handling one HTTP request in a web app).
# Example in a web API:
# db = SessionLocal()  # create session for this request
# user = db.query(User).filter(User.id == 1).first()
# db.close()   
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# Purpose: This is the base class for all your ORM models.
# Any class that represents a table in your database should inherit from Base:
Base = declarative_base()