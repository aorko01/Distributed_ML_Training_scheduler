"""Unit tests for app/services/watchdog_service.py."""
import asyncio
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.models.job_model import JobStatus
from app.services import watchdog_service
from conftest import make_job, make_user


class TestLastHeartbeatTs:
    @pytest.mark.asyncio()
    async def test_returns_int(self, fake_redis):
        fake_redis.store["worker_heartbeat:w1"] = "999"
        with patch.object(watchdog_service, "redis_client", fake_redis):
            assert await watchdog_service._last_heartbeat_ts("w1") == 999

    @pytest.mark.asyncio()
    async def test_missing_and_garbage_return_none(self, fake_redis):
        fake_redis.store["worker_heartbeat:w1"] = "bad"
        with patch.object(watchdog_service, "redis_client", fake_redis):
            assert await watchdog_service._last_heartbeat_ts("w1") is None
            assert await watchdog_service._last_heartbeat_ts("ghost") is None


class TestMarkRetryNeeded:
    def test_marks_and_clears_device(self, db):
        user = make_user(db)
        job = make_job(
            db, user.user_id, status=JobStatus.IN_PROGRESS, device="A100",
            started_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        watchdog_service._mark_retry_needed(db, job)
        assert job.status == JobStatus.RETRY_NEEDED
        assert job.device is None
        assert job.gpu_hour == pytest.approx(1.0, abs=0.05)
        assert "heartbeat" in job.failure_reason

    def test_keeps_existing_failure_reason(self, db):
        user = make_user(db)
        job = make_job(
            db, user.user_id, status=JobStatus.IN_PROGRESS,
            failure_reason="custom",
        )
        watchdog_service._mark_retry_needed(db, job)
        assert job.failure_reason == "custom"


class TestCleanupStaleJobWorkers:
    @pytest.mark.asyncio()
    async def test_stale_keys_deleted(self, fake_redis):
        fake_redis.store["job_worker:running"] = "w1"
        fake_redis.store["job_worker:done"] = "w1"
        with patch.object(watchdog_service, "redis_client", fake_redis):
            await watchdog_service._cleanup_stale_job_workers(db=None, running_ids={"running"})
        assert "job_worker:done" not in fake_redis.store
        assert "job_worker:running" in fake_redis.store


class TestCheckStalledJobs:
    def _patch_session(self, db):
        class _Ctx:
            def query(self, *a, **k):
                return db.query(*a, **k)

            def commit(self):
                db.commit()

            def refresh(self, obj):
                db.refresh(obj)

            def close(self):
                pass

        return _Ctx()

    @pytest.mark.asyncio()
    async def test_stalled_job_marked(self, db, fake_redis):
        user = make_user(db)
        job = make_job(db, user.user_id, status=JobStatus.IN_PROGRESS)
        fake_redis.store[f"job_worker:{job.id}"] = "w1"
        fake_redis.store["worker_heartbeat:w1"] = str(
            int(time.time()) - watchdog_service.STALL_TIMEOUT_SECONDS - 10
        )
        with (
            patch.object(watchdog_service, "redis_client", fake_redis),
            patch.object(
                watchdog_service, "SessionLocal",
                return_value=self._patch_session(db),
            ),
        ):
            marked = await watchdog_service.check_stalled_jobs()
        assert marked == 1
        db.refresh(job)
        assert job.status == JobStatus.RETRY_NEEDED

    @pytest.mark.asyncio()
    async def test_fresh_heartbeat_not_marked(self, db, fake_redis):
        user = make_user(db)
        job = make_job(db, user.user_id, status=JobStatus.IN_PROGRESS)
        fake_redis.store[f"job_worker:{job.id}"] = "w1"
        fake_redis.store["worker_heartbeat:w1"] = str(int(time.time()))
        with (
            patch.object(watchdog_service, "redis_client", fake_redis),
            patch.object(
                watchdog_service, "SessionLocal",
                return_value=self._patch_session(db),
            ),
        ):
            marked = await watchdog_service.check_stalled_jobs()
        assert marked == 0
        db.refresh(job)
        assert job.status == JobStatus.IN_PROGRESS

    @pytest.mark.asyncio()
    async def test_job_without_mapping_skipped(self, db, fake_redis):
        user = make_user(db)
        job = make_job(db, user.user_id, status=JobStatus.IN_PROGRESS)
        with (
            patch.object(watchdog_service, "redis_client", fake_redis),
            patch.object(
                watchdog_service, "SessionLocal",
                return_value=self._patch_session(db),
            ),
        ):
            marked = await watchdog_service.check_stalled_jobs()
        assert marked == 0
        db.refresh(job)
        assert job.status == JobStatus.IN_PROGRESS

    @pytest.mark.asyncio()
    async def test_missing_heartbeat_counts_as_stalled(self, db, fake_redis):
        user = make_user(db)
        job = make_job(db, user.user_id, status=JobStatus.IN_PROGRESS)
        fake_redis.store[f"job_worker:{job.id}"] = "w1"
        with (
            patch.object(watchdog_service, "redis_client", fake_redis),
            patch.object(
                watchdog_service, "SessionLocal",
                return_value=self._patch_session(db),
            ),
        ):
            marked = await watchdog_service.check_stalled_jobs()
        assert marked == 1


class TestRunStallWatcher:
    @pytest.mark.asyncio()
    async def test_loop_sleeps_between_scans(self):
        calls = {"n": 0}

        async def fake_check():
            calls["n"] += 1
            if calls["n"] >= 2:
                raise asyncio.CancelledError()
            return 0

        with (
            patch.object(watchdog_service, "check_stalled_jobs", side_effect=fake_check),
            patch.object(watchdog_service.asyncio, "sleep", new=AsyncMock()) as mock_sleep,
        ):
            with pytest.raises(asyncio.CancelledError):
                await watchdog_service.run_stall_watcher()
        assert mock_sleep.await_count >= 1
