"""Unit tests for Scheduler pydantic schemas and ORM enums."""
import pytest
from pydantic import ValidationError

from app.models.job_model import JobPriority, JobStatus
from app.schemas.heartbeat_schema import HeartbeatResponse, HeartbeatSchema
from app.schemas.job_schema import (
    InteractiveBuildRequest,
    InteractiveReadyRequest,
    JobFailureReport,
    JobIDRequest,
    JobResumeRequest,
    VramEstimationReport,
)
from app.schemas.log_schema import LogLinesRequest
from app.schemas.resource_schema import ResourceConfig, ResourceRequestCreate
from app.schemas.user_schema import Token, TokenData, UserCreate, UserLogin
from app.schemas.worker_schema import WorkerInfo, WorkerResource


class TestJobStatusEnum:
    def test_all_expected_members(self):
        assert {s.value for s in JobStatus} == {
            "NOT_RUNNABLE", "VRAM_ESTIMATION_PENDING", "RUNNABLE", "IN_PROGRESS",
            "COMPLETED", "FAILED", "RETRY_NEEDED", "INTERACTIVE_READY",
        }

    def test_priority_members(self):
        assert {p.value for p in JobPriority} == {"NORMAL", "REQUESTED", "HIGH"}


class TestJobSchemas:
    def test_failure_report_literal(self):
        assert JobFailureReport(job_id="j", failure_type="user").failure_type == "user"
        assert JobFailureReport(job_id="j", failure_type="system").failure_type == "system"
        with pytest.raises(ValidationError):
            JobFailureReport(job_id="j", failure_type="bogus")

    def test_vram_report_requires_floats(self):
        with pytest.raises(ValidationError):
            VramEstimationReport(job_id="j", vram_required=1.0, ram_required=1.0)
        ok = VramEstimationReport(
            job_id="j", vram_required=1.0, ram_required=2.0, step_time=0.5
        )
        assert ok.step_time == 0.5

    def test_resume_request_device_optional(self):
        assert JobResumeRequest(job_id="j", worker_id="w").device is None

    def test_interactive_requests(self):
        assert InteractiveBuildRequest(base_job_id="b").name is None
        assert InteractiveReadyRequest(job_id="j").job_id == "j"

    def test_job_id_request(self):
        assert JobIDRequest(job_id="x").job_id == "x"


class TestWorkerSchemas:
    def test_worker_info_required_fields(self):
        with pytest.raises(ValidationError):
            WorkerInfo(worker_id="w")
        ok = WorkerInfo(worker_id="w", gpu_type="A100", num_gpus=1, total_vram=40.0)
        assert ok.hostname is None

    def test_worker_resource(self):
        r = WorkerResource(worker_id="w", gpu_type="A100", free_vram=10.0)
        assert r.free_vram == 10.0

    def test_heartbeat_required(self):
        with pytest.raises(ValidationError):
            HeartbeatSchema(worker_id="w", gpu_type="A100")
        hb = HeartbeatSchema(worker_id="w", gpu_type="A100", available_vram=5.0)
        assert hb.gpus_in_use is None
        assert HeartbeatResponse(status="success", worker_id="w").status == "success"


class TestUserSchemas:
    def test_user_create_validates_email(self):
        with pytest.raises(ValidationError):
            UserCreate(username="u", email="not-an-email", password="pw")
        ok = UserCreate(username="u", email="u@example.com", password="pw")
        assert ok.name is None

    def test_login_and_token(self):
        assert UserLogin(username="u", password="p").username == "u"
        assert Token(access_token="abc").token_type == "bearer"
        assert TokenData(user_id="1").username is None


class TestResourceSchemas:
    def test_config_defaults(self):
        cfg = ResourceConfig()
        assert cfg.op == "ge"
        assert cfg.gpu_type is None

    def test_request_create_all_optional(self):
        req = ResourceRequestCreate()
        assert req.notes is None


class TestLogSchema:
    def test_default_empty_list(self):
        assert LogLinesRequest().lines == []
        assert LogLinesRequest(lines=["a"]).lines == ["a"]
