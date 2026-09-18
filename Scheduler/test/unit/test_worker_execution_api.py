import json
import secrets
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.worker_execution_route import router
from app.api.deps import get_db
from test.unit.test_runtime_scheduling import worker, inventory, identifier


def client(db, monkeypatch, tmp_path, worker_id):
    token = secrets.token_urlsafe(48)
    path = tmp_path / "worker-secrets"
    path.write_text(json.dumps({worker_id: [token]}))
    path.chmod(0o600)
    monkeypatch.setenv("WORKER_CREDENTIALS_FILE", str(path))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app), {"Authorization": "Bearer " + token}


def test_authentication_identity_and_strict_body(db, monkeypatch, tmp_path):
    w = worker(db)
    c, headers = client(db, monkeypatch, tmp_path, w.worker_id)
    body = {"instance_id": w.instance_id, "request_id": identifier()}
    assert c.post("/internal/workers/v1/claim", json=body).status_code == 401
    assert (
        c.post(
            "/internal/workers/v1/claim",
            headers=headers,
            json={**body, "worker_id": w.worker_id},
        ).status_code
        == 422
    )
    assert (
        c.post("/internal/workers/v1/claim", headers=headers, json=body).status_code
        == 200
    )


def test_rotation_and_revocation(db, monkeypatch, tmp_path):
    w = worker(db)
    c, headers = client(db, monkeypatch, tmp_path, w.worker_id)
    path = tmp_path / "worker-secrets"
    replacement = secrets.token_urlsafe(48)
    path.write_text(json.dumps({w.worker_id: [replacement]}))
    assert (
        c.post(
            "/internal/workers/v1/claim",
            headers=headers,
            json={"instance_id": w.instance_id, "request_id": identifier()},
        ).status_code
        == 401
    )


def test_no_heartbeat_assignments_and_stale_sequence(db, monkeypatch, tmp_path):
    w = worker(db)
    c, headers = client(db, monkeypatch, tmp_path, w.worker_id)
    body = {
        "instance_id": w.instance_id,
        "sequence": 1,
        "paused": True,
        "draining": False,
        "inventory": inventory(),
        "assignments": [],
    }
    response = c.post("/internal/workers/v1/heartbeat", headers=headers, json=body)
    assert response.status_code == 200 and response.json()["decisions"] == []
    assert (
        c.post("/internal/workers/v1/heartbeat", headers=headers, json=body).status_code
        == 409
    )
    assert w.execution_paused


def test_authenticated_heartbeat_mirrors_dashboard_telemetry(
    db, monkeypatch, tmp_path
):
    w = worker(db)
    c, headers = client(db, monkeypatch, tmp_path, w.worker_id)
    body = {
        "instance_id": w.instance_id,
        "sequence": 1,
        "paused": False,
        "draining": False,
        "inventory": {
            **inventory(),
            "hostname": "gpu-host",
            "ip_address": "10.0.0.5",
            "cpu_load": 12.5,
            "mem_usage": 37.5,
            "total_ram": 64.0,
            "total_disk": 500.0,
            "available_disk": 220.0,
            "gpu_load": 9.0,
            "gpus_in_use": 1,
        },
        "assignments": [],
    }
    response = c.post(
        "/internal/workers/v1/heartbeat", headers=headers, json=body
    )
    assert response.status_code == 200
    assert w.hostname == "gpu-host"
    assert w.mem_usage == 37.5
    assert w.total_ram == 64.0
    assert w.available_disk == 220.0
    assert w.gpu_load == 9.0
