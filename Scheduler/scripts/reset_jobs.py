"""Clear all jobs + interactive/job-related data for a fresh start.

KEPT: users, workers, worker_credentials, cli_refresh_tokens, worker
liveness keys in Redis.
CLEARED (Postgres): jobs, resource_requests, interactive_workspaces,
interactive_image_revisions, interactive_runtimes, worker_assignments,
workspace_save_operations, workspace_snapshot_artifacts,
workspace_training_submissions.
CLEARED (Redis): logs:*, build_logs:*, job_worker:*,
image_build_attempt_heartbeat:*, image_builder_heartbeat:*.

Usage (from Scheduler/):
    DATABASE_URL=postgresql://... REDIS_HOST=... REDIS_PORT=... \\
        python3 scripts/reset_jobs.py [--yes]

Without --yes it only prints what WOULD be cleared.
"""

import asyncio
import os
import sys

TABLES = [
    "worker_assignments",
    "workspace_snapshot_artifacts",
    "workspace_training_submissions",
    "workspace_save_operations",
    "interactive_runtimes",
    "interactive_image_revisions",
    "interactive_workspaces",
    "jobs",
    "resource_requests",
]

REDIS_PATTERNS = [
    "logs:*",
    "build_logs:*",
    "job_worker:*",
    "image_build_attempt_heartbeat:*",
    "image_builder_heartbeat:*",
]


def clear_postgres() -> dict[str, int]:
    from sqlalchemy import create_engine, text

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is not set")
    engine = create_engine(database_url)
    counts: dict[str, int] = {}
    with engine.begin() as conn:
        for table in TABLES:
            n = conn.execute(
                text(f"SELECT count(*) FROM {table}")
            ).scalar()
            counts[table] = int(n or 0)
        # Single multi-table TRUNCATE: all referencing tables are listed, so
        # no CASCADE is needed and users/workers/credentials are untouched.
        conn.execute(text(f"TRUNCATE TABLE {', '.join(TABLES)}"))
    return counts


async def clear_redis() -> dict[str, int]:
    import redis.asyncio as redis

    client = redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", 6379)),
        decode_responses=True,
    )
    counts: dict[str, int] = {}
    for pattern in REDIS_PATTERNS:
        keys = [k async for k in client.scan_iter(match=pattern)]
        if keys:
            await client.delete(*keys)
        counts[pattern] = len(keys)
    await client.aclose()
    return counts


def main() -> None:
    confirmed = "--yes" in sys.argv[1:]
    print("Postgres tables to clear:", ", ".join(TABLES))
    print("Redis patterns to clear:", ", ".join(REDIS_PATTERNS))
    print("Kept: users, workers, worker_credentials, cli_refresh_tokens, "
          "worker liveness keys")
    if not confirmed:
        print("\nDry run. Re-run with --yes to actually delete.")
        return
    pg = clear_postgres()
    rd = asyncio.run(clear_redis())
    print("\nDeleted Postgres rows:")
    for table, n in pg.items():
        print(f"  {table}: {n}")
    print("Deleted Redis keys:")
    for pattern, n in rd.items():
        print(f"  {pattern}: {n}")
    print("\nDone. Scheduler/workers pick up the empty state without a restart.")


if __name__ == "__main__":
    main()
