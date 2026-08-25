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
        engine = create_engine(DATABASE_URL) # This object knows how to connect to the database
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


def run_migrations():
    """Apply lightweight additive schema migrations on startup
    (for development; production should use Alembic)."""
    from sqlalchemy import text

    statements = [
        "ALTER TABLE workers ADD COLUMN IF NOT EXISTS gpus_in_use INTEGER",
        "ALTER TABLE workers ADD COLUMN IF NOT EXISTS total_disk FLOAT",
        "ALTER TABLE workers ADD COLUMN IF NOT EXISTS available_disk FLOAT",
        "ALTER TABLE workers ADD COLUMN IF NOT EXISTS cpu_cores INTEGER",
        "ALTER TABLE workers ADD COLUMN IF NOT EXISTS total_ram FLOAT",
        "ALTER TABLE resource_requests ADD COLUMN IF NOT EXISTS disk FLOAT",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS resume_command VARCHAR",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS name VARCHAR",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS failure_reason VARCHAR",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS ram_required FLOAT",
        "ALTER TABLE workers ADD COLUMN IF NOT EXISTS is_testing BOOLEAN",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS build_type VARCHAR DEFAULT 'training'",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS base_job_id VARCHAR",
        # Interactive jobs have no zip archive / explicit base image
        "ALTER TABLE jobs ALTER COLUMN object_key DROP NOT NULL",
        "ALTER TABLE jobs ALTER COLUMN docker_base_image DROP NOT NULL",
        # At most one interactive session per base job (DB-level guard)
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_jobs_base_job_interactive "
        "ON jobs (base_job_id) WHERE build_type = 'interactive'",
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))