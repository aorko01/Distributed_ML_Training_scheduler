import os
import time

import requests

from app.core.redis import redis_client

LOG_STREAM_PREFIX = "logs:"
LOG_STREAM_MAXLEN = 10000

OBJECT_STORE_URL = os.environ.get(
    "OBJECT_STORE_URL", "http://localhost:8010"
).rstrip("/")
OBJECT_OUTPUT_BUCKET = os.environ.get("OBJECT_OUTPUT_BUCKET", "outputs")

TERMINAL_STATUSES = {"COMPLETED", "FAILED"}


def _stream_key(job_id: str) -> str:
    return f"{LOG_STREAM_PREFIX}{job_id}"


async def publish_log_lines(job_id: str, lines: list[str]) -> None:
    """Push log lines from producers into the job's Redis stream."""
    if not lines:
        return

    ts = int(time.time() * 1000)
    pipe = redis_client.pipeline(transaction=False)
    for line in lines:
        pipe.xadd(
            _stream_key(job_id),
            {"line": line, "ts": ts},
            maxlen=LOG_STREAM_MAXLEN,
            approximate=True,
        )
    await pipe.execute()


async def get_log_stream_history(job_id: str) -> list[dict]:
    """Return every entry currently in the job's Redis stream (oldest first)."""
    entries = await redis_client.xrange(_stream_key(job_id), "-", "+")
    return [
        {
            "id": entry_id,
            "line": data.get("line", ""),
            "ts": int(data.get("ts", 0)),
        }
        for entry_id, data in entries
    ]


async def read_log_stream(
    job_id: str, last_id: str, count: int = 100, block_ms: int = 2000
) -> list[dict]:
    """Block until new entries appear in the job's stream, returning them oldest first."""
    result = await redis_client.xread(
        streams={_stream_key(job_id): last_id}, count=count, block=block_ms
    )

    messages = []
    for _stream_name, entries in result:
        for entry_id, data in entries:
            messages.append(
                {
                    "id": entry_id,
                    "line": data.get("line", ""),
                    "ts": int(data.get("ts", 0)),
                }
            )
    return messages


def fetch_build_log_from_object_store(job_id: str) -> str:
    """Fetch the full {job_id}/build.log written by the builder/worker. Empty string if absent."""
    url = f"{OBJECT_STORE_URL}/objects/{OBJECT_OUTPUT_BUCKET}/{job_id}/build.log"
    try:
        response = requests.get(url, timeout=30)
        if response.status_code == 404:
            return ""
        response.raise_for_status()
        return response.text
    except Exception:
        return ""
