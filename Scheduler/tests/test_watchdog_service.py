"""Unit tests for Scheduler/app/services/watchdog_service.py — pending job stall detection."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timezone, timedelta  # noqa: E402
from unittest.mock import MagicMock, patch  # noqa: E402
import pytest  # noqa: E402

from app.models.job_model import JobStatus  # noqa: E402


# ---------------------------------------------------------------------------
# check_stalled_pending_jobs
# ---------------------------------------------------------------------------
class TestCheckStalledPendingJobs:
    """Tests for the pending-job stall watchdog using timezone-aware datetimes."""

    def _make_pending_job(self, updated_at=None, created_at=None):
        """Create a mock pending job with the given timestamps."""
        job = MagicMock()
        job.id = "pending-job-1"
        job.status = JobStatus.PENDING
        job.updated_at = updated_at
        job.created_at = created_at
        job.failure_reason = None
        return job

    @pytest.mark.asyncio
    async def test_resets_stalled_pending_job_when_updated_at_none(self):
        """When updated_at is None but created_at is 40 minutes ago,
        the job should be reset to NOT_RUNNABLE."""
        from app.services.watchdog_service import check_stalled_pending_jobs

        old_time = datetime.now(timezone.utc) - timedelta(minutes=40)
        job = self._make_pending_job(updated_at=None, created_at=old_time)

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = [job]

        with patch("app.services.watchdog_service.SessionLocal", return_value=mock_db):
            result = await check_stalled_pending_jobs()

        assert result == 1
        assert job.status == JobStatus.NOT_RUNNABLE
        mock_db.commit.assert_called()

    @pytest.mark.asyncio
    async def test_uses_updated_at_when_present(self):
        """When updated_at is present and stale (40 min ago), the job
        should be reset to NOT_RUNNABLE."""
        from app.services.watchdog_service import check_stalled_pending_jobs

        old_time = datetime.now(timezone.utc) - timedelta(minutes=40)
        job = self._make_pending_job(updated_at=old_time, created_at=old_time)

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = [job]

        with patch("app.services.watchdog_service.SessionLocal", return_value=mock_db):
            result = await check_stalled_pending_jobs()

        assert result == 1
        assert job.status == JobStatus.NOT_RUNNABLE

    @pytest.mark.asyncio
    async def test_does_not_reset_fresh_job(self):
        """When updated_at is None and created_at is recent (5 min ago),
        the job should NOT be reset."""
        from app.services.watchdog_service import check_stalled_pending_jobs

        fresh_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        job = self._make_pending_job(updated_at=None, created_at=fresh_time)

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = [job]

        with patch("app.services.watchdog_service.SessionLocal", return_value=mock_db):
            result = await check_stalled_pending_jobs()

        assert result == 0
        assert job.status == JobStatus.PENDING
        mock_db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_job_when_both_timestamps_none(self):
        """When both updated_at and created_at are None, the job should be
        skipped entirely — no reset, no commit."""
        from app.services.watchdog_service import check_stalled_pending_jobs

        job = self._make_pending_job(updated_at=None, created_at=None)

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = [job]

        with patch("app.services.watchdog_service.SessionLocal", return_value=mock_db):
            result = await check_stalled_pending_jobs()

        assert result == 0
        assert job.status == JobStatus.PENDING
        mock_db.commit.assert_not_called()
