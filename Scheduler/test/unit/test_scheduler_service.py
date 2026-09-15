"""Unit tests for app/services/scheduler_service.py."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.models.job_model import JobStatus
from app.services import scheduler_service
from test.helpers import make_job, make_user, make_worker


class TestGetOverview:
    @pytest.mark.asyncio()
    async def test_aggregates_workers_and_queue(self, db):
        user = make_user(db)
        make_worker(db, worker_id="w1", num_gpus=2)
        make_worker(db, worker_id="w2", num_gpus=4)
        make_job(db, user.user_id, status=JobStatus.RUNNABLE)
        make_job(db, user.user_id, status=JobStatus.VRAM_ESTIMATION_PENDING)
        make_job(db, user.user_id, status=JobStatus.COMPLETED)
        live = [
            {"gpu_load": 50.0, "gpus_in_use": 1, "num_gpus": 2},
            {"gpu_load": 100.0, "gpus_in_use": None, "num_gpus": 4},
        ]
        with patch.object(
            scheduler_service, "get_all_workers", new=AsyncMock(return_value=live)
        ):
            overview = await scheduler_service.get_overview(db)
        assert overview["nodes_total"] == 2
        assert overview["nodes_online"] == 2
        assert overview["gpus_total"] == 6
        assert overview["queue_depth"] == 2
        assert overview["cluster_load"] == pytest.approx(75.0)
        # second worker falls back to num_gpus * load estimate: 4 * 100% = 4
        assert overview["gpus_allocated"] == 1 + 4

    @pytest.mark.asyncio()
    async def test_empty_cluster(self, db):
        with patch.object(
            scheduler_service, "get_all_workers", new=AsyncMock(return_value=[])
        ):
            overview = await scheduler_service.get_overview(db)
        assert overview == {
            "nodes_online": 0,
            "nodes_total": 0,
            "cluster_load": 0.0,
            "queue_depth": 0,
            "gpus_allocated": 0,
            "gpus_total": 0,
        }


class TestCompletionTime:
    def test_prefers_updated_at(self, db):
        user = make_user(db)
        job = make_job(db, user.user_id)
        job.updated_at = datetime(2024, 5, 1, 12, 0, tzinfo=timezone.utc)
        job.created_at = datetime(2024, 4, 1, 12, 0, tzinfo=timezone.utc)
        assert scheduler_service._completion_time(job).date().isoformat() == "2024-05-01"

    def test_naive_datetime_assumed_utc(self, db):
        user = make_user(db)
        job = make_job(db, user.user_id)
        job.updated_at = None
        job.created_at = datetime(2024, 5, 1, 12, 0)
        out = scheduler_service._completion_time(job)
        assert out.tzinfo is not None

    def test_both_none_returns_none(self, db):
        user = make_user(db)
        job = make_job(db, user.user_id)
        job.updated_at = None
        job.created_at = None
        assert scheduler_service._completion_time(job) is None


class TestBuckets:
    def _completed(self, *dts):
        return [(dt, f"id-{i}") for i, dt in enumerate(dts)]

    def test_daily_has_six_buckets(self):
        now = datetime.now(timezone.utc)
        out = scheduler_service._bucket_daily(self._completed(now))
        assert len(out) == 6
        assert sum(b["jobs"] for b in out) == 1

    def test_weekly_has_seven_buckets(self):
        now = datetime.now(timezone.utc)
        out = scheduler_service._bucket_weekly(self._completed(now, now))
        assert len(out) == 7
        assert sum(b["jobs"] for b in out) == 2

    def test_monthly_has_four_weeks(self):
        now = datetime.now(timezone.utc)
        out = scheduler_service._bucket_monthly(self._completed(now))
        assert len(out) == 4
        assert sum(b["jobs"] for b in out) == 1

    def test_yearly_has_twelve_months(self):
        now = datetime.now(timezone.utc)
        out = scheduler_service._bucket_yearly(self._completed(now))
        assert len(out) == 12
        assert sum(b["jobs"] for b in out) == 1

    def test_old_entries_ignored(self):
        old = datetime(2000, 1, 1, tzinfo=timezone.utc)
        assert sum(b["jobs"] for b in scheduler_service._bucket_daily([ (old, "x") ])) == 0


class TestGetThroughput:
    def test_completed_jobs_bucketed(self, db):
        user = make_user(db)
        now = datetime.now(timezone.utc)
        job = make_job(db, user.user_id, status=JobStatus.COMPLETED)
        job.updated_at = now
        db.commit()
        out = scheduler_service.get_throughput(db)
        assert set(out.keys()) == {"daily", "weekly", "monthly", "yearly"}
        assert sum(b["jobs"] for b in out["daily"]) == 1

    def test_no_completed_jobs_all_zeros(self, db):
        out = scheduler_service.get_throughput(db)
        assert sum(b["jobs"] for b in out["weekly"]) == 0
