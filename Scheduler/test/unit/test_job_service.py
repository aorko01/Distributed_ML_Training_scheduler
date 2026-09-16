"""Unit tests for app/services/job_service.py."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.job_model import JobPriority, JobStatus
from app.models.worker_model import Worker
from app.schemas.worker_schema import WorkerResource
from app.services import job_service
from test.helpers import make_job, make_user, make_worker


def _request(worker_id="w1", gpu_type="NVIDIA A100", free_vram=20.0):
    return WorkerResource(worker_id=worker_id, gpu_type=gpu_type, free_vram=free_vram)


class TestCreateJob:
    def test_create_job_persists_fields(self, db):
        user = make_user(db)
        job = job_service.create_job(
            db,
            {
                "id": "job-1",
                "user_id": user.user_id,
                "object_key": "job-1/a.zip",
                "command": "python train.py",
                "docker_base_image": "pytorch/pytorch:2.1",
            },
        )
        assert job.id == "job-1"
        assert job.status == JobStatus.NOT_RUNNABLE
        assert job.priority == JobPriority.NORMAL

    def test_create_job_with_priority_and_extras(self, db):
        user = make_user(db)
        job = job_service.create_job(
            db,
            {
                "id": "job-2",
                "user_id": user.user_id,
                "object_key": "job-2/a.zip",
                "name": "exp",
                "command": "python train.py",
                "resume_command": "python resume.py",
                "docker_base_image": "img",
                "priority": JobPriority.REQUESTED,
                "reason_for_priority": "deadline",
            },
        )
        assert job.priority == JobPriority.REQUESTED
        assert job.reason_for_priority == "deadline"
        assert job.resume_command == "python resume.py"


class TestStateTransitions:
    def test_not_runnable_to_pending(self, db):
        user = make_user(db)
        job = make_job(db, user.user_id, status=JobStatus.IMAGE_BUILDING)
        out = job_service.set_job_vram_estimation_pending(db, job.id)
        assert out.status == JobStatus.VRAM_ESTIMATION_PENDING

    def test_pending_requires_not_runnable(self, db):
        user = make_user(db)
        job = make_job(db, user.user_id, status=JobStatus.RUNNABLE)
        with pytest.raises(Exception, match="Job is not in IMAGE_BUILDING state"):
            job_service.set_job_vram_estimation_pending(db, job.id)

    def test_pending_to_runnable(self, db):
        user = make_user(db)
        job = make_job(db, user.user_id, status=JobStatus.VRAM_ESTIMATION_PENDING)
        out = job_service.set_job_runnable(db, job.id)
        assert out.status == JobStatus.RUNNABLE

    def test_runnable_requires_pending(self, db):
        user = make_user(db)
        job = make_job(db, user.user_id)
        with pytest.raises(Exception, match="VRAM_ESTIMATION_PENDING"):
            job_service.set_job_runnable(db, job.id)

    def test_missing_job_raises(self, db):
        with pytest.raises(Exception, match="Job not found"):
            job_service.set_job_vram_estimation_pending(db, "nope")
        with pytest.raises(Exception, match="Job not found"):
            job_service.set_job_runnable(db, "nope")
        with pytest.raises(Exception, match="Job not found"):
            job_service.save_vram_estimation(db, "nope", 1.0, 1.0, 1.0)

    def test_save_vram_estimation_marks_runnable(self, db):
        user = make_user(db)
        job = make_job(db, user.user_id, status=JobStatus.VRAM_ESTIMATION_PENDING)
        out = job_service.save_vram_estimation(db, job.id, 4.0, 8.0, 1.5)
        assert out.status == JobStatus.RUNNABLE
        assert out.vram_required == 4.0
        assert out.ram_required == 8.0
        assert out.step_time == 1.5


class TestGetNotRunnableJobs:
    def test_only_not_runnable_returned(self, db):
        user = make_user(db)
        j1 = make_job(db, user.user_id, status=JobStatus.NOT_RUNNABLE)
        make_job(db, user.user_id, status=JobStatus.RUNNABLE)
        jobs = job_service.get_not_runnable_jobs(db)
        assert [j["id"] for j in jobs] == [j1.id]


class TestClaimAndReleaseForBuilding:
    def test_claim_uses_skip_locked_row_lock(self):
        db = MagicMock()
        query = db.query.return_value
        query.filter.return_value = query
        query.order_by.return_value = query
        query.with_for_update.return_value = query
        query.first.return_value = None

        assert job_service.claim_job_for_building(db, "builder-1") is None
        query.with_for_update.assert_called_once_with(skip_locked=True)

    def test_claim_job_success(self, db):
        user = make_user(db)
        job = make_job(db, user.user_id, status=JobStatus.NOT_RUNNABLE)
        claimed = job_service.claim_job_for_building(db, "builder-1")
        assert claimed is not None
        assert claimed["id"] == job.id
        assert claimed["status"] == JobStatus.IMAGE_BUILDING.value
        assert claimed["flag"] == "image_building"
        assert claimed["image_builder_id"] == "builder-1"
        assert claimed["image_build_attempt_id"]

    def test_claim_job_no_jobs(self, db):
        assert job_service.claim_job_for_building(db, "builder-1") is None

    def test_release_job_success(self, db):
        user = make_user(db)
        job = make_job(
            db,
            user.user_id,
            status=JobStatus.IMAGE_BUILDING,
            image_builder_id="builder-1",
            image_build_attempt_id="attempt-1",
        )
        released = job_service.release_job_to_not_runnable(
            db, job.id, "builder-1", "attempt-1"
        )
        assert released.status == JobStatus.NOT_RUNNABLE
        assert released.image_build_attempt_id is None

    def test_release_job_not_found(self, db):
        with pytest.raises(Exception, match="Job not found"):
            job_service.release_job_to_not_runnable(
                db, "nonexistent-job", "builder-1", "attempt-1"
            )

    def test_release_job_wrong_status(self, db):
        user = make_user(db)
        job = make_job(db, user.user_id, status=JobStatus.NOT_RUNNABLE)
        with pytest.raises(Exception, match="Job is not in IMAGE_BUILDING state"):
            job_service.release_job_to_not_runnable(
                db, job.id, "builder-1", "attempt-1"
            )

    def test_stale_attempt_cannot_release_reassigned_job(self, db):
        user = make_user(db)
        job = make_job(
            db,
            user.user_id,
            status=JobStatus.IMAGE_BUILDING,
            image_builder_id="builder-2",
            image_build_attempt_id="attempt-2",
        )
        with pytest.raises(Exception, match="Stale or unowned"):
            job_service.release_job_to_not_runnable(
                db, job.id, "builder-1", "attempt-1"
            )
        db.refresh(job)
        assert job.status == JobStatus.IMAGE_BUILDING
        assert job.image_build_attempt_id == "attempt-2"

    def test_ready_callback_records_attempt_tag_and_clears_lease(self, db):
        user = make_user(db)
        job = make_job(
            db,
            user.user_id,
            status=JobStatus.IMAGE_BUILDING,
            image_builder_id="builder-1",
            image_build_attempt_id="attempt-1",
        )
        result = job_service.set_job_vram_estimation_pending(
            db,
            job.id,
            "builder-1",
            "attempt-1",
            "repo/job:build-attempt-1",
        )
        assert result.status == JobStatus.VRAM_ESTIMATION_PENDING
        assert result.image_tag == "repo/job:build-attempt-1"
        assert result.image_build_attempt_id is None



class TestVramEstimationStrategy:
    @pytest.mark.asyncio()
    async def test_highest_vram_worker_gets_pending_job(self, db, fake_redis):
        user = make_user(db)
        job = make_job(db, user.user_id, status=JobStatus.VRAM_ESTIMATION_PENDING)
        make_worker(db, worker_id="w1")
        fake_redis.hashes["worker:w1"] = {"available_vram": "20"}
        fake_redis.hashes["worker:w2"] = {"available_vram": "10"}
        with patch.object(job_service, "redis_client", fake_redis):
            out = await job_service._check_vram_estimation_strategy(db, _request("w1"))
        assert out is not None
        assert out["flag"] == "vram_estimation"
        assert out["id"] == job.id
        assert fake_redis.store[f"job_worker:{job.id}"] == "w1"

    @pytest.mark.asyncio()
    async def test_lower_vram_worker_gets_nothing(self, db, fake_redis):
        user = make_user(db)
        make_job(db, user.user_id, status=JobStatus.VRAM_ESTIMATION_PENDING)
        with patch.object(job_service, "redis_client", fake_redis):
            fake_redis.hashes["worker:w1"] = {"available_vram": "5"}
            fake_redis.hashes["worker:w2"] = {"available_vram": "20"}
            out = await job_service._check_vram_estimation_strategy(
                db, _request("w1", free_vram=5.0)
            )
        assert out is None

    @pytest.mark.asyncio()
    async def test_no_pending_job_returns_none(self, db, fake_redis):
        with patch.object(job_service, "redis_client", fake_redis):
            assert (
                await job_service._check_vram_estimation_strategy(db, _request("w1"))
                is None
            )

    @pytest.mark.asyncio()
    async def test_is_highest_with_no_workers_is_true(self, fake_redis):
        with patch.object(job_service, "redis_client", fake_redis):
            assert await job_service._is_highest_vram_worker(1.0) is True

    @pytest.mark.asyncio()
    async def test_get_connected_vrams_skips_bad_values(self, fake_redis):
        fake_redis.hashes["worker:a"] = {"available_vram": "not-a-float"}
        fake_redis.hashes["worker:b"] = {"available_vram": "8"}
        with patch.object(job_service, "redis_client", fake_redis):
            assert await job_service._get_connected_workers_vram() == [8.0]


class TestTrainingAndRetryStrategies:
    @pytest.mark.asyncio()
    async def test_training_picks_largest_fitting_vram(self, db, fake_redis):
        user = make_user(db)
        small = make_job(
            db, user.user_id, status=JobStatus.RUNNABLE, vram_required=2.0
        )
        big = make_job(db, user.user_id, status=JobStatus.RUNNABLE, vram_required=8.0)
        too_big = make_job(
            db, user.user_id, status=JobStatus.RUNNABLE, vram_required=100.0
        )
        with patch.object(job_service, "redis_client", fake_redis):
            out = await job_service._check_training_job_strategy(
                db, _request(free_vram=20.0)
            )
        assert out["id"] == big.id
        assert out["flag"] == "training"
        db.refresh(big)
        assert big.status == JobStatus.IN_PROGRESS
        assert big.device == "NVIDIA A100"

    @pytest.mark.asyncio()
    async def test_training_respects_one_gb_buffer(self, db, fake_redis):
        user = make_user(db)
        # needs 10 + 1.0 buffer = 11 > 10 free -> must not be scheduled
        make_job(db, user.user_id, status=JobStatus.RUNNABLE, vram_required=10.0)
        with patch.object(job_service, "redis_client", fake_redis):
            out = await job_service._check_training_job_strategy(
                db, _request(free_vram=10.0)
            )
        assert out is None

    @pytest.mark.asyncio()
    async def test_training_allows_null_vram(self, db, fake_redis):
        user = make_user(db)
        job = make_job(db, user.user_id, status=JobStatus.RUNNABLE, vram_required=None)
        with patch.object(job_service, "redis_client", fake_redis):
            out = await job_service._check_training_job_strategy(
                db, _request(free_vram=1.0)
            )
        assert out["id"] == job.id

    @pytest.mark.asyncio()
    async def test_retry_strategy(self, db, fake_redis):
        user = make_user(db)
        job = make_job(
            db, user.user_id, status=JobStatus.RETRY_NEEDED, vram_required=2.0
        )
        with patch.object(job_service, "redis_client", fake_redis):
            out = await job_service._check_retry_job_strategy(db, _request())
        assert out["id"] == job.id
        assert out["flag"] == "retry"


class TestGetNextJobForWorker:
    @pytest.mark.asyncio()
    async def test_unknown_worker_raises(self, db, fake_redis):
        with patch.object(job_service, "redis_client", fake_redis):
            with pytest.raises(Exception, match="Worker not found"):
                await job_service.get_next_job_for_worker(db, _request("ghost"))

    @pytest.mark.asyncio()
    async def test_testing_worker_gets_nothing(self, db, fake_redis):
        user = make_user(db)
        make_job(db, user.user_id, status=JobStatus.RUNNABLE)
        make_worker(db, worker_id="w-test", is_testing=True)
        with patch.object(job_service, "redis_client", fake_redis):
            assert (
                await job_service.get_next_job_for_worker(db, _request("w-test"))
                is None
            )

    @pytest.mark.asyncio()
    async def test_retry_preferred_over_training(self, db, fake_redis):
        user = make_user(db)
        make_job(db, user.user_id, status=JobStatus.RUNNABLE, vram_required=1.0)
        retry = make_job(db, user.user_id, status=JobStatus.RETRY_NEEDED)
        make_worker(db, worker_id="w1")
        with patch.object(job_service, "redis_client", fake_redis):
            out = await job_service.get_next_job_for_worker(db, _request("w1"))
        assert out["id"] == retry.id

    @pytest.mark.asyncio()
    async def test_no_jobs_returns_none(self, db, fake_redis):
        make_worker(db, worker_id="w1")
        with patch.object(job_service, "redis_client", fake_redis):
            assert await job_service.get_next_job_for_worker(db, _request("w1")) is None


class TestGetJobForResume:
    @pytest.mark.asyncio()
    async def test_resume_success(self, db, fake_redis):
        user = make_user(db)
        job = make_job(
            db, user.user_id, status=JobStatus.IN_PROGRESS, device="NVIDIA A100"
        )
        fake_redis.store[f"job_worker:{job.id}"] = "w1"
        with patch.object(job_service, "redis_client", fake_redis):
            out = await job_service.get_job_for_resume(db, job.id, "w1", "NVIDIA A100")
        assert out is not None
        assert out["flag"] == "retry"

    @pytest.mark.asyncio()
    async def test_resume_wrong_worker_returns_none(self, db, fake_redis):
        user = make_user(db)
        job = make_job(db, user.user_id, status=JobStatus.IN_PROGRESS)
        fake_redis.store[f"job_worker:{job.id}"] = "other"
        with patch.object(job_service, "redis_client", fake_redis):
            assert await job_service.get_job_for_resume(db, job.id, "w1") is None

    @pytest.mark.asyncio()
    async def test_resume_device_mismatch_returns_none(self, db, fake_redis):
        user = make_user(db)
        job = make_job(
            db, user.user_id, status=JobStatus.IN_PROGRESS, device="NVIDIA A100"
        )
        fake_redis.store[f"job_worker:{job.id}"] = "w1"
        with patch.object(job_service, "redis_client", fake_redis):
            assert (
                await job_service.get_job_for_resume(db, job.id, "w1", "NVIDIA H100")
                is None
            )

    @pytest.mark.asyncio()
    async def test_resume_non_in_progress_returns_none(self, db, fake_redis):
        user = make_user(db)
        job = make_job(db, user.user_id, status=JobStatus.COMPLETED)
        with patch.object(job_service, "redis_client", fake_redis):
            assert await job_service.get_job_for_resume(db, job.id, "w1") is None


class TestCompletionAndFailure:
    def test_set_to_completed_computes_gpu_hours(self, db):
        user = make_user(db)
        started = datetime.now(timezone.utc) - timedelta(hours=2)
        job = make_job(
            db, user.user_id, status=JobStatus.IN_PROGRESS, started_at=started
        )
        out = job_service.set_to_completed(db, job.id)
        assert out.status == JobStatus.COMPLETED
        assert out.gpu_hour == pytest.approx(2.0, abs=0.05)

    def test_set_to_completed_requires_in_progress(self, db):
        user = make_user(db)
        job = make_job(db, user.user_id)
        with pytest.raises(Exception, match="IN_PROGRESS"):
            job_service.set_to_completed(db, job.id)

    def test_set_to_completed_missing_raises(self, db):
        with pytest.raises(Exception, match="Job not found"):
            job_service.set_to_completed(db, "nope")

    def test_mark_failed_user_vs_system(self, db):
        user = make_user(db)
        j1 = make_job(db, user.user_id)
        assert (
            job_service.mark_job_failed(db, j1.id, "user", "bad code").status
            == JobStatus.FAILED
        )
        j2 = make_job(db, user.user_id)
        assert (
            job_service.mark_job_failed(db, j2.id, "system", "daemon down").status
            == JobStatus.RETRY_NEEDED
        )

    def test_mark_failed_truncates_reason(self, db):
        user = make_user(db)
        job = make_job(db, user.user_id)
        out = job_service.mark_job_failed(db, job.id, "user", "x" * 5000)
        assert len(out.failure_reason) == 2000

    def test_mark_failed_terminal_raises(self, db):
        user = make_user(db)
        job = make_job(db, user.user_id, status=JobStatus.COMPLETED)
        with pytest.raises(Exception, match="terminal state"):
            job_service.mark_job_failed(db, job.id, "user", "late")

    def test_mark_failed_missing_raises(self, db):
        with pytest.raises(Exception, match="Job not found"):
            job_service.mark_job_failed(db, "nope", "user")


class TestUserJobQueries:
    def test_counts_and_gpu_hours(self, db):
        u1 = make_user(db)
        u2 = make_user(db)
        make_job(db, u1.user_id, gpu_hour=1.5)
        make_job(db, u1.user_id, gpu_hour=None)
        make_job(db, u2.user_id, gpu_hour=2.0)
        assert job_service.get_user_jobs_count(db, u1.user_id) == 2
        assert job_service.get_user_gpu_hours(db, u1.user_id) == pytest.approx(1.5)
        assert job_service.get_runnable_jobs_count(db) == 0

    def test_get_user_jobs_ordered_desc(self, db):
        user = make_user(db)
        make_job(db, user.user_id)
        jobs = job_service.get_user_jobs(db, user.user_id)
        assert len(jobs) == 1
        assert jobs[0]["status"] == JobStatus.NOT_RUNNABLE.value

    def test_get_user_job_by_id(self, db):
        user = make_user(db)
        job = make_job(db, user.user_id)
        out = job_service.get_user_job_by_id(db, user.user_id, job.id)
        assert out["id"] == job.id
        assert job_service.get_user_job_by_id(db, user.user_id, "nope") is None
        other = make_user(db)
        assert job_service.get_user_job_by_id(db, other.user_id, job.id) is None
