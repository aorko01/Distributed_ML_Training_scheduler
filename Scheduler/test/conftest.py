"""Shared fixtures for Scheduler unit tests.

- Forces an in-memory SQLite DB *before* any ``app`` module is imported
  (``app.db.database`` connects on import).
- Puts the ``Scheduler/`` directory on ``sys.path`` so ``import app...``
  works regardless of where pytest is invoked from.
- Provides a fresh transactional SQLite session per test plus small
  factories for users / jobs / workers and an in-memory async Redis fake.
"""
import os
import sys
from unittest.mock import AsyncMock

import pytest

# ---------------------------------------------------------------------------
# Environment must be set before importing anything under app/
# ---------------------------------------------------------------------------
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("REDIS_HOST", "localhost")
os.environ.setdefault("REDIS_PORT", "6379")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30")
os.environ.setdefault("OBJECT_STORE_URL", "http://localhost:8010")
os.environ.setdefault("OBJECT_STORE_BUCKET", "uploads")

SCHEDULER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCHEDULER_DIR not in sys.path:
    sys.path.insert(0, SCHEDULER_DIR)

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.db.database import Base  # noqa: E402
import app.models.user_model  # noqa: E402,F401
import app.models.job_model  # noqa: E402,F401
import app.models.worker_model  # noqa: E402,F401
import app.models.resource_request_model  # noqa: E402,F401

import app.models.interactive_workspace_model  # noqa: E402,F401

import app.models.interactive_runtime_model
import app.models.cli_token_model  # noqa: F401  (SSH scoped refresh tokens)

TEST_ENGINE = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(bind=TEST_ENGINE)
TestingSessionLocal = sessionmaker(
    autocommit=False, autoflush=False, bind=TEST_ENGINE
)


@pytest.fixture()
def db():
    """Fresh transactional session rolled back after each test."""
    connection = TEST_ENGINE.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


class FakeAsyncRedis:
    """Minimal in-memory async Redis stand-in for unit tests."""

    def __init__(self):
        self.store = {}
        self.hashes = {}
        self.expirations = {}
        self.deleted = []
        self.streams = {}

    async def keys(self, pattern):
        import fnmatch

        all_keys = list(self.store.keys()) + list(self.hashes.keys())
        # streams are stored under their key too when used via stream helpers
        all_keys += [k for k in self.streams.keys() if k not in all_keys]
        return [k for k in all_keys if fnmatch.fnmatch(k, pattern)]

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    async def hset(self, key, mapping=None, **kwargs):
        self.hashes.setdefault(key, {}).update(mapping or {})
        self.hashes[key].update(kwargs)
        return True

    async def expire(self, key, ttl):
        self.expirations[key] = ttl
        return True

    async def set(self, key, value, ex=None):
        self.store[key] = str(value)
        if ex is not None:
            self.expirations[key] = ex
        return True

    async def get(self, key):
        return self.store.get(key)

    async def exists(self, key):
        return 1 if (key in self.store or key in self.hashes) else 0

    async def delete(self, *keys):
        count = 0
        for key in keys:
            self.deleted.append(key)
            for container in (self.store, self.hashes, self.streams):
                if key in container:
                    del container[key]
                    count += 1
        return count

    # -- streams ---------------------------------------------------------
    def pipeline(self, transaction=False):
        redis = self

        class _Pipe:
            def __init__(self):
                self.ops = []

            def xadd(self, key, fields, maxlen=None, approximate=None):
                self.ops.append((key, fields))
                return self

            async def execute(self):
                for key, fields in self.ops:
                    redis.streams.setdefault(key, []).append(
                        (f"{len(redis.streams.get(key, [])) + 1}-0", dict(fields))
                    )
                return [True] * len(self.ops)

        return _Pipe()

    async def xrange(self, key, min_id="-", max_id="+"):
        return list(self.streams.get(key, []))

    async def xread(self, streams=None, count=100, block=2000):
        result = []
        for key, last_id in (streams or {}).items():
            entries = [
                (eid, data)
                for eid, data in self.streams.get(key, [])
                if eid > str(last_id)
            ][:count]
            if entries:
                result.append((key, entries))
        return result


@pytest.fixture()
def fake_redis():
    return FakeAsyncRedis()


@pytest.fixture()
def mock_redis():
    """Generic AsyncMock redis client for patching service modules."""
    client = AsyncMock()
    client.keys.return_value = []
    client.hget.return_value = None
    client.get.return_value = None
    client.exists.return_value = 0
    client.set.return_value = True
    client.hset.return_value = True
    client.expire.return_value = True
    client.delete.return_value = 1
    client.xrange.return_value = []
    client.xread.return_value = []
    return client


from test.helpers import make_job, make_user, make_worker  # noqa: F401
