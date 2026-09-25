"""Unit tests for GET /jobs/{job_id}/output/download."""

import io
import os
import zipfile
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps, jobs_route
from app.services import output_service
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


async def _fake_chunks(client, *, bucket, object_key, size, force_presigned):
    yield f"content:{object_key}".encode()


class TestDownloadJobOutput:
    def test_success_streams_zip_without_temp_file(self, db):
        user = make_user(db)
        job = make_job(
            db,
            user.user_id,
            name="My Training Run",
            object_key="placeholder/archive.zip",
        )
        entries = [
            ("uploads", "placeholder/archive.zip", "submitted/archive.zip", None, True),
            ("outputs", f"{job.id}/build.log", "outputs/build.log", 8, False),
        ]
        client = _client(db, user)

        with (
            patch.object(
                jobs_route.output_service,
                "collect_job_output_entries",
                return_value=entries,
            ) as collect,
            patch.object(
                jobs_route.output_service,
                "_afetch_object_chunks",
                side_effect=_fake_chunks,
            ),
        ):
            response = client.get(f"/{job.id}/output/download")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        disposition = response.headers["content-disposition"]
        assert "attachment" in disposition
        assert "my_training_run-output.zip" in disposition
        # Chunked streaming: no content-length, first byte out immediately.
        assert response.content[:2] == b"PK"
        collect.assert_called_once_with(job.id, job.object_key)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert set(archive.namelist()) == {
                "submitted/archive.zip",
                "outputs/build.log",
            }
            assert archive.read("outputs/build.log") == (
                f"content:{job.id}/build.log".encode()
            )

    def test_streams_large_binary_without_deflate_roundtrip(self, db):
        """A multi-chunk binary entry must arrive byte-identical."""
        user = make_user(db)
        job = make_job(db, user.user_id, name="Big Run")
        big = os.urandom(2 * 1024 * 1024 + 17)

        async def _big_chunks(client, **kwargs):
            for offset in range(0, len(big), 1 << 20):
                yield big[offset : offset + (1 << 20)]

        client = _client(db, user)
        with (
            patch.object(
                jobs_route.output_service,
                "collect_job_output_entries",
                return_value=[("outputs", f"{job.id}/model.pt", "outputs/model.pt", len(big), False)],
            ),
            patch.object(
                jobs_route.output_service,
                "_afetch_object_chunks",
                side_effect=_big_chunks,
            ),
        ):
            response = client.get(f"/{job.id}/output/download")

        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert archive.read("outputs/model.pt") == big

    def test_job_not_owned_returns_404_without_listing(self, db):
        owner = make_user(db)
        other = make_user(db)
        job = make_job(db, owner.user_id)
        client = _client(db, other)
        with patch.object(
            jobs_route.output_service, "collect_job_output_entries"
        ) as collect:
            response = client.get(f"/{job.id}/output/download")
        assert response.status_code == 404
        assert response.json()["detail"] == "Job not found"
        collect.assert_not_called()

    def test_no_files_returns_404(self, db):
        user = make_user(db)
        job = make_job(db, user.user_id)
        client = _client(db, user)
        with patch.object(
            jobs_route.output_service,
            "collect_job_output_entries",
            return_value=[],
        ):
            response = client.get(f"/{job.id}/output/download")
        assert response.status_code == 404
        assert "No output files" in response.json()["detail"]

    def test_invalid_object_key_returns_400(self, db):
        user = make_user(db)
        job = make_job(db, user.user_id)
        client = _client(db, user)
        with patch.object(
            jobs_route.output_service,
            "collect_job_output_entries",
            side_effect=ValueError("Submitted object key does not belong to this job"),
        ):
            response = client.get(f"/{job.id}/output/download")
        assert response.status_code == 400

    def test_object_store_failure_returns_502(self, db):
        user = make_user(db)
        job = make_job(db, user.user_id)
        client = _client(db, user)
        with patch.object(
            jobs_route.output_service,
            "collect_job_output_entries",
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

    def test_output_service_module_importable(self):
        assert callable(output_service.aiter_entries_as_zip)
