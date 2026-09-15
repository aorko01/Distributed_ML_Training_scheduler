"""Unit tests for Scheduler FastAPI routes (auth/worker/scheduler/resource/jobs)."""
import io
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.api import auth_route, docker_route, jobs_route, resource_route
from app.api import scheduler_route, worker_route
from app.models.job_model import JobStatus
from app.utils.auth import create_access_token, get_password_hash
from test.helpers import make_job, make_user, make_worker


def _client_with(router, db, user=None, db_dep=None):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[db_dep or jobs_route.get_db] = lambda: db
    if user is not None:
        app.dependency_overrides[deps.get_current_active_user] = lambda: user
        app.dependency_overrides[deps.get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# auth_route
# ---------------------------------------------------------------------------
class TestAuthRoutes:
    def _client(self, db):
        app = FastAPI()
        app.include_router(auth_route.router)
        app.dependency_overrides[auth_route.get_db] = lambda: db
        return TestClient(app, raise_server_exceptions=False)

    def test_register_success(self, db):
        client = self._client(db)
        resp = client.post(
            "/auth/register",
            json={"username": "alice", "email": "alice@example.com", "password": "pw"},
        )
        assert resp.status_code == 201
        assert resp.json()["username"] == "alice"

    def test_register_duplicate_username(self, db):
        make_user(db, username="bob", email="bob@example.com")
        client = self._client(db)
        resp = client.post(
            "/auth/register",
            json={"username": "bob", "email": "other@example.com", "password": "pw"},
        )
        assert resp.status_code == 400

    def test_register_duplicate_email(self, db):
        make_user(db, username="carol", email="carol@example.com")
        client = self._client(db)
        resp = client.post(
            "/auth/register",
            json={"username": "other", "email": "carol@example.com", "password": "pw"},
        )
        assert resp.status_code == 400

    def test_login_success_and_failure(self, db):
        make_user(db, username="dave", password_hash=get_password_hash("correct"))
        client = self._client(db)
        ok = client.post("/auth/login", json={"username": "dave", "password": "correct"})
        assert ok.status_code == 200
        assert "access_token" in ok.json()
        bad = client.post("/auth/login", json={"username": "dave", "password": "wrong"})
        assert bad.status_code == 401


# ---------------------------------------------------------------------------
# deps
# ---------------------------------------------------------------------------
class TestDeps:
    def test_get_db_yields_and_closes(self):
        gen = deps.get_db()
        db = next(gen)
        assert db is not None
        with pytest.raises(StopIteration):
            next(gen)

    def test_get_current_user_invalid_token(self, db):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            deps.get_current_user(token="bad", db=db)
        assert exc.value.status_code == 401

    def test_get_current_user_missing_user(self, db):
        from fastapi import HTTPException

        token = create_access_token({"sub": "ghost", "username": "ghost"})
        with pytest.raises(HTTPException):
            deps.get_current_user(token=token, db=db)

    def test_get_current_user_success(self, db):
        user = make_user(db)
        token = create_access_token({"sub": user.user_id, "username": user.username})
        assert deps.get_current_user(token=token, db=db).user_id == user.user_id

    def test_inactive_user_rejected(self, db):
        from fastapi import HTTPException

        user = make_user(db)
        user.is_active = False
        with pytest.raises(HTTPException):
            deps.get_current_active_user(current_user=user)


# ---------------------------------------------------------------------------
# worker_route
# ---------------------------------------------------------------------------
class TestWorkerRoutes:
    def _client(self, db):
        app = FastAPI()
        app.include_router(worker_route.router)
        app.dependency_overrides[worker_route.get_db] = lambda: db
        return TestClient(app, raise_server_exceptions=False)

    def test_register(self, db):
        client = self._client(db)
        resp = client.post(
            "/register",
            json={"worker_id": "w1", "gpu_type": "A100", "num_gpus": 2,
                  "total_vram": 80.0},
        )
        assert resp.status_code == 200
        assert resp.json()["worker_id"] == "w1"

    def test_heartbeat_not_registered_404(self, db):
        client = self._client(db)
        with patch.object(
            worker_route.worker_service, "process_heartbeat",
            new=AsyncMock(return_value=False),
        ):
            resp = client.post(
                "/heartbeat",
                json={"worker_id": "ghost", "gpu_type": "A100", "available_vram": 1.0},
            )
        assert resp.status_code == 404

    def test_heartbeat_success(self, db):
        client = self._client(db)
        with patch.object(
            worker_route.worker_service, "process_heartbeat",
            new=AsyncMock(return_value=True),
        ):
            resp = client.post(
                "/heartbeat",
                json={"worker_id": "w1", "gpu_type": "A100", "available_vram": 1.0},
            )
        assert resp.status_code == 200

    def test_total_gpus_and_nodes(self, db):
        make_worker(db, worker_id="w1", num_gpus=3)
        client = self._client(db)
        assert client.get("/total_gpus").json() == {"total_gpus": 3}
        with patch.object(
            worker_route.worker_service, "get_all_workers",
            new=AsyncMock(return_value=[]),
        ):
            assert client.get("/nodes").json() == {"nodes": []}


# ---------------------------------------------------------------------------
# scheduler_route
# ---------------------------------------------------------------------------
class TestSchedulerRoutes:
    def _client(self, db):
        app = FastAPI()
        app.include_router(scheduler_route.router)
        app.dependency_overrides[scheduler_route.get_db] = lambda: db
        return TestClient(app, raise_server_exceptions=False)

    def test_health(self, db):
        assert self._client(db).get("/health").json() == {
            "status": "ok", "service": "scheduler"
        }

    def test_overview_and_throughput(self, db):
        client = self._client(db)
        with patch.object(
            scheduler_route.scheduler_service, "get_overview",
            new=AsyncMock(return_value={"nodes_online": 1}),
        ):
            assert client.get("/overview").json() == {"nodes_online": 1}
        with patch.object(
            scheduler_route.scheduler_service, "get_throughput",
            return_value={"daily": []},
        ):
            assert client.get("/throughput").json() == {"daily": []}


# ---------------------------------------------------------------------------
# resource_route
# ---------------------------------------------------------------------------
class TestResourceRoutes:
    def _client(self, db, user):
        app = FastAPI()
        app.include_router(resource_route.router)
        app.dependency_overrides[deps.get_db] = lambda: db
        app.dependency_overrides[deps.get_current_user] = lambda: user
        return TestClient(app, raise_server_exceptions=False)

    def test_options(self, db):
        user = make_user(db)
        client = self._client(db, user)
        with patch.object(
            resource_route.resource_service, "get_resource_options",
            return_value={"gpu_types": []},
        ):
            # response_model validation requires full shape; use real service instead
            pass
        resp = client.get("/options")
        assert resp.status_code == 200
        assert "gpu_types" in resp.json()

    def test_summary(self, db):
        user = make_user(db)
        client = self._client(db, user)
        resp = client.post("/summary", json={})
        assert resp.status_code == 200
        assert "matching_nodes" in resp.json()

    def test_request_creates_queue_entry(self, db):
        user = make_user(db)
        client = self._client(db, user)
        resp = client.post("/request", json={"gpu_type": "A100"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "PENDING"
        assert body["queue_open"] == 1


# ---------------------------------------------------------------------------
# docker_route via TestClient
# ---------------------------------------------------------------------------
class TestDockerRouteClient:
    def test_pytorch_tags_client(self):
        app = FastAPI()
        app.include_router(docker_route.router)
        client = TestClient(app, raise_server_exceptions=False)
        with patch.object(
            docker_route, "_fetch_all_tags",
            return_value=["2.1-cuda11.8-cudnn8-runtime"],
        ):
            resp = client.get("/pytorch-tags")
        assert resp.status_code == 200
        assert resp.json()[0]["version"] == "2.1"


# ---------------------------------------------------------------------------
# jobs_route
# ---------------------------------------------------------------------------
class TestJobsRoutes:
    def _client(self, db, user=None):
        app = FastAPI()
        app.include_router(jobs_route.router)
        app.dependency_overrides[jobs_route.get_db] = lambda: db
        if user is not None:
            app.dependency_overrides[deps.get_current_active_user] = lambda: user
        return TestClient(app, raise_server_exceptions=False)

    def test_update_to_pending_and_runnable(self, db):
        user = make_user(db)
        job = make_job(db, user.user_id, status=JobStatus.IMAGE_BUILDING)
        client = self._client(db)
        resp = client.post("/update_job_to_vram_estimation_pending", json={"job_id": job.id})
        assert resp.json()["status"] == "VRAM_ESTIMATION_PENDING"
        resp = client.post("/update_job_to_runnable", json={"job_id": job.id})
        assert resp.json()["status"] == "RUNNABLE"

    def test_update_missing_returns_error(self, db):
        client = self._client(db)
        resp = client.post(
            "/update_job_to_vram_estimation_pending", json={"job_id": "nope"}
        )
        assert "error" in resp.json()

    def test_unbuilt_jobs(self, db):
        user = make_user(db)
        make_job(db, user.user_id)
        client = self._client(db)
        assert len(client.get("/unbuilt_jobs").json()["jobs"]) == 1

    def test_save_vram_estimation(self, db):
        user = make_user(db)
        job = make_job(db, user.user_id, status=JobStatus.VRAM_ESTIMATION_PENDING)
        client = self._client(db)
        resp = client.post(
            "/save_vram_estimation",
            json={"job_id": job.id, "vram_required": 4.0, "ram_required": 8.0,
                  "step_time": 1.0},
        )
        assert resp.json()["vram_required"] == 4.0

    def test_pull_job_none_and_job(self, db):
        client = self._client(db)
        with patch.object(
            jobs_route.job_service, "get_next_job_for_worker",
            new=AsyncMock(return_value=None),
        ):
            assert "message" in client.post(
                "/pull_job",
                json={"worker_id": "w", "gpu_type": "A100", "free_vram": 10.0},
            ).json()
        with patch.object(
            jobs_route.job_service, "get_next_job_for_worker",
            new=AsyncMock(return_value={"id": "j", "flag": "training"}),
        ):
            assert client.post(
                "/pull_job",
                json={"worker_id": "w", "gpu_type": "A100", "free_vram": 10.0},
            ).json()["id"] == "j"

    def test_resume(self, db):
        client = self._client(db)
        with patch.object(
            jobs_route.job_service, "get_job_for_resume",
            new=AsyncMock(return_value=None),
        ):
            resp = client.post(
                "/resume", json={"job_id": "j", "worker_id": "w"}
            )
            assert "message" in resp.json()

    def test_mark_completed_and_failed(self, db):
        user = make_user(db)
        job = make_job(db, user.user_id, status=JobStatus.IN_PROGRESS)
        client = self._client(db)
        assert client.post("/mark_completed", json={"job_id": job.id}).json()["status"] == "COMPLETED"
        job2 = make_job(db, user.user_id)
        resp = client.post(
            "/mark_failed",
            json={"job_id": job2.id, "failure_type": "user", "failure_reason": "bad"},
        )
        assert resp.json()["status"] == "FAILED"

    def test_mark_failed_bad_type_is_422(self, db):
        client = self._client(db)
        resp = client.post(
            "/mark_failed", json={"job_id": "j", "failure_type": "bogus"}
        )
        assert resp.status_code == 422

    def test_ingest_logs(self, db):
        client = self._client(db)
        with patch.object(
            jobs_route.log_service, "publish_log_lines",
            new=AsyncMock(return_value=None),
        ):
            assert client.post("/logs/j1", json={"lines": ["a"]}).json() == {"ok": True}

    def test_authed_endpoints(self, db):
        user = make_user(db)
        job = make_job(db, user.user_id, status=JobStatus.RUNNABLE)
        client = self._client(db, user)
        assert client.get("/queue_length").json() == {"queue_length": 1}
        assert len(client.get("/mine").json()["jobs"]) == 1
        assert client.get("/mine/count").json() == {"count": 1}
        assert "gpu_hours" in client.get("/mine/gpu_hours").json()
        assert client.get(f"/{job.id}").json()["id"] == job.id
        assert "error" in client.get("/does-not-exist").json()

    def test_get_job_logs(self, db):
        user = make_user(db)
        job = make_job(db, user.user_id)
        client = self._client(db, user)
        with patch.object(
            jobs_route.log_service, "fetch_build_log_from_object_store",
            return_value="content",
        ):
            resp = client.get(f"/{job.id}/logs")
        assert resp.json()["content"] == "content"

    def test_get_job_logs_not_found(self, db):
        user = make_user(db)
        client = self._client(db, user)
        assert "error" in client.get("/missing/logs").json()

    def test_submit_job_success(self, db):
        user = make_user(db)
        client = self._client(db, user)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("requirements.txt", "torch")
        with patch.object(
            jobs_route, "save_to_object_store",
            return_value={"object_key": "jid/a.zip", "files": []},
        ):
            resp = client.post(
                "/submit_job",
                files={"zip_file": ("a.zip", buf.getvalue(), "application/zip")},
                data={"command": "python train.py", "docker_base_image": "img"},
            )
        assert resp.json()["object_key"] == "jid/a.zip"

    def test_submit_job_store_failure(self, db):
        user = make_user(db)
        client = self._client(db, user)
        with patch.object(
            jobs_route, "save_to_object_store",
            side_effect=Exception("bad zip"),
        ):
            resp = client.post(
                "/submit_job",
                files={"zip_file": ("a.zip", b"data", "application/zip")},
                data={"command": "python train.py", "docker_base_image": "img"},
            )
        assert resp.json() == {"error": "bad zip"}

    def test_get_output_by_id_missing(self, db):
        client = self._client(db)
        resp = client.post("/get_output_by_id", json={"job_id": "ghost"})
        assert "error" in resp.json()


class TestWsAuthenticate:
    def test_no_token_returns_none(self, db):
        ws = MagicMock()
        ws.query_params.get.return_value = None
        assert jobs_route._ws_authenticate(ws, db) is None

    def test_valid_token_returns_user(self, db):
        user = make_user(db)
        token = create_access_token({"sub": user.user_id, "username": user.username})
        ws = MagicMock()
        ws.query_params.get.return_value = token
        assert jobs_route._ws_authenticate(ws, db).user_id == user.user_id

    def test_bad_token_returns_none(self, db):
        ws = MagicMock()
        ws.query_params.get.return_value = "garbage"
        assert jobs_route._ws_authenticate(ws, db) is None
