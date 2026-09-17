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

    if engine.dialect.name != "postgresql":
        return

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
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS image_builder_id VARCHAR",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS image_build_attempt_id VARCHAR",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS image_build_started_at TIMESTAMPTZ",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS image_build_excluded_builder_id VARCHAR",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS image_build_excluded_until TIMESTAMPTZ",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS image_tag VARCHAR",
        "ALTER TABLE workers ADD COLUMN IF NOT EXISTS is_testing BOOLEAN",
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))

        # The attempt token is the distributed fencing key.  Use a unique
        # partial index so NULL remains valid for jobs that are not building.
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_jobs_image_build_attempt_id "
            "ON jobs (image_build_attempt_id) "
            "WHERE image_build_attempt_id IS NOT NULL"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_jobs_image_builder_id "
            "ON jobs (image_builder_id)"
        ))

    # New interactive tables have an explicit production migration; create_all
    # alone cannot add composite constraints/triggers to an existing deployment.
    if engine.dialect.name == "postgresql":
        from pathlib import Path
        migration = Path(__file__).resolve().parents[2] / "migrations" / "001_interactive_workspaces.sql"
        with engine.begin() as conn:
            conn.exec_driver_sql("SELECT pg_advisory_xact_lock(764293810)")
            conn.exec_driver_sql(migration.read_text())
