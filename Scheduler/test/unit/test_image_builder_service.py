"""Tests for image-builder leases, heartbeats, and stale cancellation."""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.models.job_model import JobStatus
from app.schemas.job_schema import ImageBuilderHeartbeat
from app.services import image_builder_service, watchdog_service
from test.helpers import make_job, make_user


@pytest.mark.asyncio()
async def test_current_attempt_heartbeat_renews_lease(db, fake_redis):
    user = make_user(db)
    job = make_job(
        db,
        user.user_id,
        status=JobStatus.IMAGE_BUILDING,
        image_builder_id="builder-1",
        image_build_attempt_id="attempt-1",
        image_build_started_at=datetime.now(timezone.utc),
    )
    heartbeat = ImageBuilderHeartbeat(
        builder_id="builder-1",
        active_builds=[{"job_id": job.id, "attempt_id": "attempt-1"}],
    )

    with patch.object(image_builder_service, "redis_client", fake_redis):
        result = await image_builder_service.process_heartbeat(db, heartbeat)

    assert result["cancel_builds"] == []
    assert fake_redis.store[
        image_builder_service.ATTEMPT_HEARTBEAT_PREFIX + "attempt-1"
    ]


@pytest.mark.asyncio()
async def test_recovered_builder_is_told_to_cancel_stale_attempt(db, fake_redis):
    user = make_user(db)
    job = make_job(
        db,
        user.user_id,
        status=JobStatus.IMAGE_BUILDING,
        image_builder_id="builder-2",
        image_build_attempt_id="attempt-2",
    )
    heartbeat = ImageBuilderHeartbeat(
        builder_id="builder-1",
        active_builds=[{"job_id": job.id, "attempt_id": "attempt-1"}],
    )

    with patch.object(image_builder_service, "redis_client", fake_redis):
        result = await image_builder_service.process_heartbeat(db, heartbeat)

    assert result["cancel_builds"] == [
        {"job_id": job.id, "attempt_id": "attempt-1"}
    ]
    assert (
        image_builder_service.ATTEMPT_HEARTBEAT_PREFIX + "attempt-1"
        not in fake_redis.store
    )


class _SessionProxy:
    def __init__(self, db):
        self.db = db

    def query(self, *args, **kwargs):
        return self.db.query(*args, **kwargs)

    def commit(self):
        return self.db.commit()

    def close(self):
        pass


@pytest.mark.asyncio()
async def test_stalled_build_is_requeued_and_can_be_claimed_by_another_builder(
    db, fake_redis
):
    user = make_user(db)
    job = make_job(
        db,
        user.user_id,
        status=JobStatus.IMAGE_BUILDING,
        image_builder_id="builder-1",
        image_build_attempt_id="attempt-1",
        image_build_started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    fake_redis.store[
        image_builder_service.ATTEMPT_HEARTBEAT_PREFIX + "attempt-1"
    ] = str(int(time.time()) - image_builder_service.HEARTBEAT_TIMEOUT_SECONDS - 1)

    with (
        patch.object(watchdog_service, "redis_client", fake_redis),
        patch.object(watchdog_service, "SessionLocal", return_value=_SessionProxy(db)),
    ):
        assert await watchdog_service.check_stalled_jobs() == 1

    db.refresh(job)
    assert job.status == JobStatus.NOT_RUNNABLE
    assert job.image_builder_id is None
    assert job.image_build_attempt_id is None

    from app.services import job_service

    # Give another builder the first chance; the timed-out builder is excluded
    # for one lease window so it cannot immediately reclaim its stale job.
    assert job_service.claim_job_for_building(db, "builder-1") is None
    replacement = job_service.claim_job_for_building(db, "builder-2")
    assert replacement["id"] == job.id
    assert replacement["image_builder_id"] == "builder-2"
    assert replacement["image_build_attempt_id"] != "attempt-1"


@pytest.mark.asyncio()
async def test_missing_first_heartbeat_gets_startup_grace(db, fake_redis):
    user = make_user(db)
    job = make_job(
        db,
        user.user_id,
        status=JobStatus.IMAGE_BUILDING,
        image_builder_id="builder-1",
        image_build_attempt_id="attempt-1",
        image_build_started_at=datetime.now(timezone.utc),
    )

    with (
        patch.object(watchdog_service, "redis_client", fake_redis),
        patch.object(watchdog_service, "SessionLocal", return_value=_SessionProxy(db)),
    ):
        assert await watchdog_service.check_stalled_jobs() == 0

    db.refresh(job)
    assert job.status == JobStatus.IMAGE_BUILDING


@pytest.mark.asyncio()
async def test_fresh_attempt_heartbeat_is_not_requeued(db, fake_redis):
    user = make_user(db)
    job = make_job(
        db,
        user.user_id,
        status=JobStatus.IMAGE_BUILDING,
        image_builder_id="builder-1",
        image_build_attempt_id="attempt-1",
        image_build_started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    fake_redis.store[
        image_builder_service.ATTEMPT_HEARTBEAT_PREFIX + "attempt-1"
    ] = str(int(time.time()))

    with (
        patch.object(watchdog_service, "redis_client", fake_redis),
        patch.object(watchdog_service, "SessionLocal", return_value=_SessionProxy(db)),
    ):
        assert await watchdog_service.check_stalled_jobs() == 0

    db.refresh(job)
    assert job.status == JobStatus.IMAGE_BUILDING
