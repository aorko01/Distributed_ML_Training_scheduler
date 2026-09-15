"""Unit tests for app/services/worker_service.py."""
from unittest.mock import patch

import pytest

from app.schemas.heartbeat_schema import HeartbeatSchema
from app.schemas.worker_schema import WorkerInfo
from app.services import worker_service
from test.helpers import make_job, make_user, make_worker


def _worker_info(worker_id="w1", **overrides):
    params = {
        "worker_id": worker_id,
        "gpu_type": "NVIDIA A100",
        "num_gpus": 2,
        "total_vram": 80.0,
    }
    params.update(overrides)
    return WorkerInfo(**params)


def _heartbeat(worker_id="w1", **overrides):
    params = {
        "worker_id": worker_id,
        "gpu_type": "NVIDIA A100",
        "available_vram": 40.0,
    }
    params.update(overrides)
    return HeartbeatSchema(**params)


class TestGetTotalGpus:
    def test_empty_is_zero(self, db):
        assert worker_service.get_total_gpus(db) == 0

    def test_sums_across_workers(self, db):
        make_worker(db, worker_id="a", num_gpus=2)
        make_worker(db, worker_id="b", num_gpus=4)
        assert worker_service.get_total_gpus(db) == 6


class TestRegisterOrUpdate:
    def test_register_creates_worker(self, db):
        worker = worker_service.register_or_update_worker_service(
            db, _worker_info("new", hostname="h1", available_vram=70.0)
        )
        assert worker.worker_id == "new"
        assert worker.hostname == "h1"
        assert worker.available_vram == 70.0

    def test_update_preserves_gpu_specs(self, db):
        make_worker(db, worker_id="w1", gpu_type="A100", num_gpus=2, total_vram=80.0)
        updated = worker_service.register_or_update_worker_service(
            db,
            _worker_info(
                "w1", gpu_type="CHANGED", num_gpus=99, total_vram=999.0, hostname="h2"
            ),
        )
        assert updated.gpu_type == "A100"  # specs not overwritten on update
        assert updated.hostname == "h2"

    def test_none_metrics_do_not_overwrite(self, db):
        make_worker(db, worker_id="w1", hostname="original")
        worker_service.register_or_update_worker_service(db, _worker_info("w1"))
        db.expire_all()
        from app.models.worker_model import Worker

        worker = db.query(Worker).filter_by(worker_id="w1").first()
        assert worker.hostname == "original"


class TestProcessHeartbeat:
    @pytest.mark.asyncio()
    async def test_heartbeat_existing_redis_key(self, fake_redis):
        fake_redis.hashes["worker:w1"] = {"available_vram": "1"}
        with (
            patch.object(worker_service, "redis_client", fake_redis),
            patch.object(
                worker_service, "_update_db_worker_metrics", return_value=None
            ) as mock_db,
        ):
            assert await worker_service.process_heartbeat(_heartbeat("w1")) is True
        mock_db.assert_awaited_once()
        assert fake_redis.expirations.get("worker:w1") == worker_service.HEARTBEAT_TTL

    @pytest.mark.asyncio()
    async def test_heartbeat_falls_back_to_db(self, db, fake_redis):
        make_worker(db, worker_id="w1")
        real_session = db

        # SessionLocal mock that yields the test session without closing it
        class _SessionCtx:
            def query(self, *a, **k):
                return real_session.query(*a, **k)

            def commit(self):
                real_session.commit()

            def close(self):
                pass

        with (
            patch.object(worker_service, "redis_client", fake_redis),
            patch.object(
                worker_service, "SessionLocal", return_value=_SessionCtx()
            ),
        ):
            assert await worker_service.process_heartbeat(_heartbeat("w1")) is True

    @pytest.mark.asyncio()
    async def test_heartbeat_unknown_worker_returns_false(self, db, fake_redis):
        class _SessionCtx:
            def query(self, *a, **k):
                return db.query(*a, **k)

            def close(self):
                pass

        with (
            patch.object(worker_service, "redis_client", fake_redis),
            patch.object(worker_service, "SessionLocal", return_value=_SessionCtx()),
        ):
            assert await worker_service.process_heartbeat(_heartbeat("ghost")) is False


class TestUpdateRedisWorker:
    @pytest.mark.asyncio()
    async def test_optional_fields_only_when_set(self, fake_redis):
        with patch.object(worker_service, "redis_client", fake_redis):
            await worker_service._update_redis_worker("worker:w1", _heartbeat("w1"))
            await worker_service._update_redis_worker(
                "worker:w2", _heartbeat("w2", gpu_load=55.0, hostname="h")
            )
        assert "gpu_load" not in fake_redis.hashes["worker:w1"]
        assert fake_redis.hashes["worker:w2"]["gpu_load"] == 55.0
        assert fake_redis.store["worker_heartbeat:w2"] is not None


class TestGetLastHeartbeat:
    @pytest.mark.asyncio()
    async def test_returns_int(self, fake_redis):
        fake_redis.store["worker_heartbeat:w1"] = "12345"
        with patch.object(worker_service, "redis_client", fake_redis):
            assert await worker_service.get_last_heartbeat("w1") == 12345

    @pytest.mark.asyncio()
    async def test_missing_returns_none(self, fake_redis):
        with patch.object(worker_service, "redis_client", fake_redis):
            assert await worker_service.get_last_heartbeat("ghost") is None

    @pytest.mark.asyncio()
    async def test_garbage_returns_none(self, fake_redis):
        fake_redis.store["worker_heartbeat:w1"] = "not-an-int"
        with patch.object(worker_service, "redis_client", fake_redis):
            assert await worker_service.get_last_heartbeat("w1") is None


class TestGetAllWorkers:
    @pytest.mark.asyncio()
    async def test_online_offline_and_running_jobs(self, db, fake_redis):
        from app.models.job_model import JobStatus

        user = make_user(db)
        make_worker(db, worker_id="online", gpu_type="A100")
        make_worker(db, worker_id="offline", gpu_type="H100")
        make_job(db, user.user_id, status=JobStatus.IN_PROGRESS, device="A100")
        fake_redis.hashes["worker:online"] = {"available_vram": "1"}
        with patch.object(worker_service, "redis_client", fake_redis):
            workers = await worker_service.get_all_workers(db)
        by_id = {w["worker_id"]: w for w in workers}
        assert by_id["online"]["status"] == "online"
        assert by_id["offline"]["status"] == "offline"
        # running_jobs counts IN_PROGRESS jobs whose device == worker.gpu_type
        assert by_id["online"]["running_jobs"] == 1
        assert by_id["offline"]["running_jobs"] == 0
