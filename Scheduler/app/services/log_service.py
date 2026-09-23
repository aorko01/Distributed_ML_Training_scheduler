import os
import time

import requests

from app.core.redis import redis_client

LOG_STREAM_PREFIX = "logs:"
BUILD_LOG_STREAM_PREFIX = "build_logs:"
LOG_STREAM_MAXLEN = 10000

OBJECT_STORE_URL = os.environ.get(
    "OBJECT_STORE_URL", "http://localhost:8010"
).rstrip("/")
OBJECT_OUTPUT_BUCKET = os.environ.get("OBJECT_OUTPUT_BUCKET", "outputs")

TERMINAL_STATUSES = {"COMPLETED", "FAILED", "RETRY_NEEDED"}

BUILD_LOG_OBJECT_KEY = "build.log"
TRAINING_LOG_OBJECT_KEY = "training.log"


def _stream_key(job_id: str, stream: str = "training") -> str:
    if stream == "build":
        return f"{BUILD_LOG_STREAM_PREFIX}{job_id}"
    return f"{LOG_STREAM_PREFIX}{job_id}"


def _normalize_stream(stream: str | None) -> str:
    return "build" if (stream or "").lower() == "build" else "training"


async def publish_log_lines(
    job_id: str, lines: list[str], stream: str | None = "training"
) -> None:
    """Push log lines from producers into the job's Redis stream.

    Builders publish with ``stream="build"`` and workers with
    ``stream="training"`` (the default) so image-build output and training
    output stay in separate streams / pages.
    """
    if not lines:
        return

    ts = int(time.time() * 1000)
    pipe = redis_client.pipeline(transaction=False)
    for line in lines:
        pipe.xadd(
            _stream_key(job_id, _normalize_stream(stream)),
            {"line": line, "ts": ts},
            maxlen=LOG_STREAM_MAXLEN,
            approximate=True,
        )
    await pipe.execute()


async def get_log_stream_history(
    job_id: str, stream: str | None = "training"
) -> list[dict]:
    """Return every entry currently in the job's Redis stream (oldest first)."""
    entries = await redis_client.xrange(
        _stream_key(job_id, _normalize_stream(stream)), "-", "+"
    )
    return [
        {
            "id": entry_id,
            "line": data.get("line", ""),
            "ts": int(data.get("ts", 0)),
        }
        for entry_id, data in entries
    ]


async def read_log_stream(
    job_id: str, last_id: str, count: int = 100, block_ms: int = 2000,
    stream: str | None = "training",
) -> list[dict]:
    """Block until new entries appear in the job's stream, returning them oldest first."""
    result = await redis_client.xread(
        streams={_stream_key(job_id, _normalize_stream(stream)): last_id},
        count=count,
        block=block_ms,
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


def _fetch_log_object(job_id: str, filename: str) -> str:
    url = f"{OBJECT_STORE_URL}/objects/{OBJECT_OUTPUT_BUCKET}/{job_id}/{filename}"
    try:
        response = requests.get(url, timeout=30)
        if response.status_code == 404:
            return ""
        response.raise_for_status()
        return response.text
    except Exception:
        return ""


def fetch_build_log_from_object_store(job_id: str) -> str:
    """Fetch the full {job_id}/build.log written by the image builder.

    Falls back to the legacy combined build.log content when present.
    """
    return _fetch_log_object(job_id, BUILD_LOG_OBJECT_KEY)


def fetch_training_log_from_object_store(job_id: str) -> str:
    """Fetch the full {job_id}/training.log written by the worker.

    Older jobs stored training output appended to build.log; fall back to
    build.log when no dedicated training.log exists so history is not lost.
    """
    content = _fetch_log_object(job_id, TRAINING_LOG_OBJECT_KEY)
    if content:
        return content
    return _fetch_log_object(job_id, BUILD_LOG_OBJECT_KEY)
