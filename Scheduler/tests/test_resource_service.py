"""Unit tests for app/services/resource_service.py."""
from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.resource_schema import ResourceConfig, ResourceRequestCreate
from app.services import resource_service
from conftest import make_user, make_worker


class TestGetResourceOptions:
    def test_distinct_sorted_values(self, db):
        make_worker(db, worker_id="a", gpu_type="B", total_vram=40.0, total_ram=64.0,
                    cpu_cores=8, available_disk=500.0)
        make_worker(db, worker_id="b", gpu_type="A", total_vram=80.0, total_ram=128.0,
                    cpu_cores=16, available_disk=1000.0)
        make_worker(db, worker_id="c", gpu_type="A", total_vram=80.0)
        opts = resource_service.get_resource_options(db)
        assert opts.gpu_types == ["A", "B"]
        assert opts.vram_options == [40.0, 80.0]
        assert 8 in opts.core_options

    def test_empty_db(self, db):
        opts = resource_service.get_resource_options(db)
        assert opts.gpu_types == []
        assert opts.vram_options == []


class TestCompare:
    def test_none_requested_always_true(self):
        assert resource_service._compare(1.0, None, "ge") is True

    def test_none_actual_is_false(self):
        assert resource_service._compare(None, 5.0, "ge") is False

    def test_ge(self):
        assert resource_service._compare(8.0, 8.0, "ge") is True
        assert resource_service._compare(4.0, 8.0, "ge") is False

    def test_eq_with_epsilon(self):
        assert resource_service._compare(8.0, 8.0, "eq") is True
        assert resource_service._compare(8.0, 9.0, "eq") is False


class TestIntOrFloat:
    def test_none(self):
        assert resource_service._int_or_float(None) is None

    def test_values(self):
        assert resource_service._int_or_float(8) == 8.0
        assert resource_service._int_or_float(8.5) == 8.5

    def test_garbage_returns_none(self):
        assert resource_service._int_or_float("abc") is None


class TestConfigMatches:
    def test_gpu_type_mismatch(self):
        cfg = ResourceConfig(gpu_type="A100")
        assert (
            resource_service._config_matches("H100", 80.0, 64.0, 8, 500.0, cfg)
            is False
        )
        assert (
            resource_service._config_matches("A100", 80.0, 64.0, 8, 500.0, cfg)
            is True
        )

    def test_empty_config_matches_everything(self):
        cfg = ResourceConfig()
        assert resource_service._config_matches("x", 1.0, 1.0, 1, 1.0, cfg) is True


class TestGetResourceSummary:
    @pytest.mark.asyncio()
    async def test_matching_and_queue_counts(self, db):
        workers = [
            {"worker_id": "w1", "gpu_type": "A100", "total_vram": 80.0,
             "total_ram": 128.0, "cpu_cores": 16, "available_disk": 1000.0,
             "running_jobs": 2},
            {"worker_id": "w2", "gpu_type": "H100", "total_vram": 80.0,
             "total_ram": 128.0, "cpu_cores": 16, "available_disk": 1000.0,
             "running_jobs": 4},
        ]
        user = make_user(db)
        resource_service.create_resource_request(
            db, user.user_id, ResourceRequestCreate(gpu_type="A100")
        )
        resource_service.create_resource_request(
            db, user.user_id, ResourceRequestCreate(gpu_type="H100")
        )
        with patch.object(
            resource_service, "get_all_workers", new=AsyncMock(return_value=workers)
        ):
            summary = await resource_service.get_resource_summary(
                db, ResourceConfig(gpu_type="A100")
            )
        assert summary.matching_nodes == 1
        assert summary.avg_running_jobs == 2.0
        assert summary.queue_total == 1
        assert summary.queue_open == 1

    @pytest.mark.asyncio()
    async def test_no_matches_zero_avg(self, db):
        with patch.object(
            resource_service, "get_all_workers", new=AsyncMock(return_value=[])
        ):
            summary = await resource_service.get_resource_summary(db, ResourceConfig())
        assert summary.matching_nodes == 0
        assert summary.avg_running_jobs == 0.0


class TestCreateResourceRequest:
    def test_creates_pending_and_counts_open(self, db):
        user = make_user(db)
        req = resource_service.create_resource_request(
            db, user.user_id, ResourceRequestCreate(gpu_type="A100", notes="need gpu")
        )
        assert req.status == "PENDING"
        assert req.gpu_type == "A100"
        assert resource_service.get_open_request_count(db) == 1
