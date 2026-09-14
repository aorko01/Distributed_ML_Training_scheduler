from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError
import os
import time

DATABASE_URL = os.getenv("DATABASE_URL")

# Retry mechanism: wait until the database is ready (useful for Docker setups)
for i in range(30):  # retry for ~30 seconds
    try:
        engine = create_engine(DATABASE_URL)
        conn = engine.connect()
        conn.close()
        print("Database connected successfully.")
        break
    except OperationalError:
        print("Database not ready yet, retrying in 1 second...")
        time.sleep(1)
else:
    raise Exception("Could not connect to database after 30 seconds.")

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

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
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))