from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError
import os
import time

# -------------------------------------------------
# DATABASE URL
# The database URL is expected to be set in the environment variable "DATABASE_URL"
# in the docker-compose file. It should be in the format:
# "postgresql://user:password@host:port/database"
# -------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")

# -------------------------------------------------
# ENGINE
# The engine is responsible for managing connections to the database.
# -------------------------------------------------
# Retry mechanism: wait until the database is ready (useful for Docker setups)
for i in range(30):  # retry for ~30 seconds
    try:
        engine = create_engine(DATABASE_URL)
        # Test connection
        conn = engine.connect()
        conn.close()
        print("Database connected successfully.")
        break
    except OperationalError:
        print("Database not ready yet, retrying in 1 second...")
        time.sleep(1)
else:
    raise Exception("Could not connect to database after 30 seconds.")

# -------------------------------------------------
# SESSION
# Session → A Unit of Work
# A session is like a workspace or a transaction scope.
# You don’t need to create a new session for every query, but you often create a session per logical operation
# (e.g., handling one HTTP request in a web app).
# Example in a web API:
# db = SessionLocal()  # create session for this request
# user = db.query(User).filter(User.id == 1).first()
# db.close()   
# -------------------------------------------------
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# -------------------------------------------------
# BASE
# Purpose: This is the base class for all your ORM models.
# Any class that represents a table in your database should inherit from Base
# -------------------------------------------------
Base = declarative_base()