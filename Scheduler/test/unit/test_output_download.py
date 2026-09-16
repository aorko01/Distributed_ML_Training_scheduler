"""Unit tests for GET /jobs/{job_id}/output/download."""

import os
import zipfile
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps, jobs_route
from test.helpers import make_job, make_user


def _client(db, user):
    app = FastAPI()
    app.include_router(jobs_route.router)
    app.dependency_overrides[jobs_route.get_db] = lambda: db
    app.dependency_overrides[deps.get_current_active_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def _zip_path(tmp_path, files: dict[str, bytes] | None = None) -> str:
    path = tmp_path / "job-output.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in (files or {"outputs/build.log": b"logs"}).items():
            archive.writestr(name, content)
    return str(path)


class TestDownloadJobOutput:
    def test_success_streams_zip_then_cleans_up_temp_file(self, db, tmp_path):
        user = make_user(db)
        job = make_job(
            db,
            user.user_id,
            name="My Training Run",
            object_key="placeholder/archive.zip",
        )
        archive_path = _zip_path(tmp_path)
        client = _client(db, user)

        with patch.object(
            jobs_route.output_service,
            "build_job_output_zip",
            return_value=archive_path,
        ) as build:
            response = client.get(f"/{job.id}/output/download")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        disposition = response.headers["content-disposition"]
        assert "attachment" in disposition
        assert "my_training_run-output.zip" in disposition
        assert response.content[:2] == b"PK"
        build.assert_called_once_with(job.id, job.object_key)
        assert not os.path.exists(archive_path)

    def test_job_not_owned_returns_404_without_building_archive(self, db):
        owner = make_user(db)
        other = make_user(db)
        job = make_job(db, owner.user_id)
        client = _client(db, other)
        with patch.object(
            jobs_route.output_service, "build_job_output_zip"
        ) as build:
            response = client.get(f"/{job.id}/output/download")
        assert response.status_code == 404
        assert response.json()["detail"] == "Job not found"
        build.assert_not_called()

    def test_no_files_returns_404(self, db):
        user = make_user(db)
        job = make_job(db, user.user_id)
        client = _client(db, user)
        with patch.object(
            jobs_route.output_service,
            "build_job_output_zip",
            side_effect=FileNotFoundError("No output files found for job_id x"),
        ):
            response = client.get(f"/{job.id}/output/download")
        assert response.status_code == 404
        assert response.json()["detail"] == "No output files found for job_id x"

    def test_invalid_object_key_returns_400(self, db):
        user = make_user(db)
        job = make_job(db, user.user_id)
        client = _client(db, user)
        with patch.object(
            jobs_route.output_service,
            "build_job_output_zip",
            side_effect=ValueError("Submitted object key does not belong to this job"),
        ):
            response = client.get(f"/{job.id}/output/download")
        assert response.status_code == 400

    def test_object_store_failure_returns_502_not_partial_zip(self, db):
        user = make_user(db)
        job = make_job(db, user.user_id)
        client = _client(db, user)
        with patch.object(
            jobs_route.output_service,
            "build_job_output_zip",
            side_effect=RuntimeError("store down"),
        ):
            response = client.get(f"/{job.id}/output/download")
        assert response.status_code == 502
        assert response.headers["content-type"].startswith("application/json")
        assert response.json()["detail"] == "store down"

    def test_requires_bearer_authentication(self, db):
        app = FastAPI()
        app.include_router(jobs_route.router)
        app.dependency_overrides[jobs_route.get_db] = lambda: db
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/some-id/output/download")

        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"

    def test_unsafe_job_id_never_reaches_db_lookup(self, db):
        user = make_user(db)
        client = _client(db, user)
        with patch.object(
            jobs_route.job_service, "get_user_job_by_id"
        ) as lookup:
            response = client.get("/..%2Fevil/output/download")

        assert response.status_code in (400, 404)
        lookup.assert_not_called()

    @pytest.mark.asyncio()
    async def test_interrupted_response_still_removes_temp_file(self, tmp_path):
        archive_path = _zip_path(tmp_path)
        response = jobs_route._TemporaryFileResponse(archive_path)

        async def receive():
            return {"type": "http.disconnect"}

        async def disconnect_on_body(message):
            if message["type"] == "http.response.body":
                raise RuntimeError("client disconnected")

        with pytest.raises(RuntimeError, match="client disconnected"):
            await response(
                {"type": "http", "method": "GET", "headers": []},
                receive,
                disconnect_on_body,
            )

        assert not os.path.exists(archive_path)
