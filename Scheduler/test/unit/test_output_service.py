"""Unit tests for app/services/output_service.py."""

import io
import os
import zipfile
from unittest.mock import MagicMock, patch

import pytest

from app.services import output_service


class StreamResponse:
    def __init__(self, chunks: list[bytes], error: Exception | None = None):
        self.chunks = chunks
        self.error = error
        self.closed = False

    def raise_for_status(self):
        if self.error:
            raise self.error

    def iter_content(self, chunk_size: int):
        assert chunk_size == output_service._CHUNK_SIZE
        yield from self.chunks

    def close(self):
        self.closed = True


def read_archive(path: str) -> dict[str, bytes]:
    try:
        with zipfile.ZipFile(path) as archive:
            return {name: archive.read(name) for name in archive.namelist()}
    finally:
        output_service.cleanup_archive(path)


def write_mock_object(archive, *, archive_name: str, **_kwargs) -> None:
    archive.writestr(archive_name, f"content:{archive_name}".encode())


class TestIsSafeJobId:
    @pytest.mark.parametrize(
        "job_id", ["abc-123", "550e8400-e29b-41d4-a716-446655440000"]
    )
    def test_accepts_valid_ids(self, job_id):
        assert output_service.is_safe_job_id(job_id) is True

    @pytest.mark.parametrize(
        "job_id", ["", ".", "..", "../etc", "a/b", "a\\b", "a\nb", "a\x7fb"]
    )
    def test_rejects_unsafe_ids(self, job_id):
        assert output_service.is_safe_job_id(job_id) is False


class TestSafeOutputRelPath:
    def test_strips_expected_prefix(self):
        assert output_service.safe_output_rel_path("j1", "j1/build.log") == "build.log"
        assert output_service.safe_output_rel_path("j1", "j1/a/b.pt") == "a/b.pt"

    @pytest.mark.parametrize(
        "key",
        [
            "j2/build.log",
            "j1/",
            "j1/../evil",
            "j1/a/../../evil",
            "j1/a/../b",
            "j1//double",
            "j1/..\\evil",
            "j1/a\x7fb",
            None,
        ],
    )
    def test_rejects_unsafe_paths(self, key):
        assert output_service.safe_output_rel_path("j1", key) is None


class TestListBucketObjects:
    def test_success_returns_objects(self):
        response = MagicMock()
        response.json.return_value = {"objects": [{"key": "j1/a"}]}
        with patch.object(
            output_service.requests, "get", return_value=response
        ) as mock_get:
            objects = output_service.list_bucket_objects("outputs", "j1/")

        assert objects == [{"key": "j1/a"}]
        mock_get.assert_called_once_with(
            f"{output_service.OBJECT_STORE_URL}/objects/list",
            params={"bucket": "outputs", "prefix": "j1/"},
            timeout=output_service._LIST_TIMEOUT,
        )

    @pytest.mark.parametrize("payload", [{}, {"objects": None}, {"objects": {}}])
    def test_invalid_payload_raises_runtime(self, payload):
        response = MagicMock()
        response.json.return_value = payload
        with (
            patch.object(output_service.requests, "get", return_value=response),
            pytest.raises(RuntimeError, match="invalid objects payload"),
        ):
            output_service.list_bucket_objects("outputs", "j1/")

    def test_http_failure_raises_runtime_instead_of_looking_empty(self):
        response = MagicMock()
        response.raise_for_status.side_effect = Exception("404 missing bucket")
        with (
            patch.object(output_service.requests, "get", return_value=response),
            pytest.raises(RuntimeError, match="Object store list failed"),
        ):
            output_service.list_bucket_objects("outputs", "j1/")


class TestPresignedDownloadUrl:
    def test_returns_url(self):
        response = MagicMock()
        response.json.return_value = {"url": "https://objects.test/download"}
        with patch.object(output_service.requests, "post", return_value=response):
            url = output_service.get_presigned_download_url("uploads", "j1/code.zip")
        assert url == "https://objects.test/download"

    @pytest.mark.parametrize("payload", [{}, {"url": ""}, {"url": None}])
    def test_missing_url_raises_runtime(self, payload):
        response = MagicMock()
        response.json.return_value = payload
        with (
            patch.object(output_service.requests, "post", return_value=response),
            pytest.raises(RuntimeError, match="Could not create download URL"),
        ):
            output_service.get_presigned_download_url("uploads", "j1/code.zip")


class TestStreamObjectToZip:
    def test_small_object_streams_through_proxy_and_closes_response(self, tmp_path):
        response = StreamResponse([b"first", b"", b"second"])
        archive_path = tmp_path / "small.zip"
        with (
            patch.object(output_service.requests, "get", return_value=response) as get,
            patch.object(output_service, "get_presigned_download_url") as presign,
            zipfile.ZipFile(archive_path, "w") as archive,
        ):
            output_service.stream_object_to_zip(
                archive,
                bucket="outputs",
                object_key="j1/build.log",
                archive_name="outputs/build.log",
                size=12,
            )

        assert response.closed is True
        presign.assert_not_called()
        get.assert_called_once_with(
            f"{output_service.OBJECT_STORE_URL}/objects/outputs/j1/build.log",
            stream=True,
            timeout=output_service._DOWNLOAD_TIMEOUT,
        )
        with zipfile.ZipFile(archive_path) as archive:
            assert archive.read("outputs/build.log") == b"firstsecond"

    def test_large_object_uses_presigned_url(self, tmp_path):
        response = StreamResponse([b"checkpoint"])
        archive_path = tmp_path / "large.zip"
        with (
            patch.object(
                output_service,
                "get_presigned_download_url",
                return_value="https://objects.test/large",
            ) as presign,
            patch.object(output_service.requests, "get", return_value=response) as get,
            zipfile.ZipFile(archive_path, "w") as archive,
        ):
            output_service.stream_object_to_zip(
                archive,
                bucket="outputs",
                object_key="j1/model.pt",
                archive_name="outputs/model.pt",
                size=output_service.OBJECT_STORE_LARGE_FILE_THRESHOLD,
            )

        presign.assert_called_once_with("outputs", "j1/model.pt")
        get.assert_called_once_with(
            "https://objects.test/large",
            stream=True,
            timeout=output_service._DOWNLOAD_TIMEOUT,
        )
        assert response.closed is True

    def test_unreachable_presigned_url_falls_back_to_streaming_proxy(self, tmp_path):
        failed_response = StreamResponse([], error=Exception("bad public endpoint"))
        proxy_response = StreamResponse([b"checkpoint"])
        archive_path = tmp_path / "fallback.zip"
        with (
            patch.object(
                output_service,
                "get_presigned_download_url",
                return_value="https://unreachable.test/large",
            ),
            patch.object(
                output_service.requests,
                "get",
                side_effect=[failed_response, proxy_response],
            ) as get,
            zipfile.ZipFile(archive_path, "w") as archive,
        ):
            output_service.stream_object_to_zip(
                archive,
                bucket="outputs",
                object_key="j1/model.pt",
                archive_name="outputs/model.pt",
                force_presigned=True,
            )

        assert [call.args[0] for call in get.call_args_list] == [
            "https://unreachable.test/large",
            f"{output_service.OBJECT_STORE_URL}/objects/outputs/j1/model.pt",
        ]
        assert failed_response.closed is True
        assert proxy_response.closed is True
        with zipfile.ZipFile(archive_path) as archive:
            assert archive.read("outputs/model.pt") == b"checkpoint"

    def test_http_failure_closes_response_and_raises(self, tmp_path):
        response = StreamResponse([], error=Exception("store unavailable"))
        with (
            patch.object(output_service.requests, "get", return_value=response),
            zipfile.ZipFile(tmp_path / "failed.zip", "w") as archive,
            pytest.raises(RuntimeError, match="Failed to download object"),
        ):
            output_service.stream_object_to_zip(
                archive,
                bucket="outputs",
                object_key="j1/build.log",
                archive_name="outputs/build.log",
            )
        assert response.closed is True


class TestBuildJobOutputZip:
    def test_builds_disk_zip_with_submitted_and_outputs(self):
        with (
            patch.object(
                output_service,
                "list_bucket_objects",
                return_value=[
                    {"key": "job1/build.log", "size": 8},
                    {"key": "job1/checkpoints/model.pt", "size": 12},
                ],
            ),
            patch.object(
                output_service,
                "stream_object_to_zip",
                side_effect=write_mock_object,
            ) as stream,
        ):
            path = output_service.build_job_output_zip("job1", "job1/code.zip")

        assert os.path.isfile(path)
        assert read_archive(path) == {
            "submitted/code.zip": b"content:submitted/code.zip",
            "outputs/build.log": b"content:outputs/build.log",
            "outputs/checkpoints/model.pt": b"content:outputs/checkpoints/model.pt",
        }
        assert stream.call_args_list[0].kwargs["force_presigned"] is True
        assert stream.call_args_list[1].kwargs["size"] == 8

    def test_outputs_only_when_no_submitted_key(self):
        with (
            patch.object(
                output_service,
                "list_bucket_objects",
                return_value=[{"key": "job1/build.log"}],
            ),
            patch.object(
                output_service,
                "stream_object_to_zip",
                side_effect=write_mock_object,
            ),
        ):
            path = output_service.build_job_output_zip("job1")
        assert read_archive(path) == {
            "outputs/build.log": b"content:outputs/build.log"
        }

    def test_skips_unsafe_list_entries(self):
        with (
            patch.object(
                output_service,
                "list_bucket_objects",
                return_value=[
                    {"key": "job1/ok.log"},
                    {"key": "job1/../evil"},
                    {"key": "other/build.log"},
                    "not-an-object",
                ],
            ),
            patch.object(
                output_service,
                "stream_object_to_zip",
                side_effect=write_mock_object,
            ) as stream,
        ):
            path = output_service.build_job_output_zip("job1")
        assert read_archive(path) == {"outputs/ok.log": b"content:outputs/ok.log"}
        assert stream.call_count == 1

    def test_any_download_failure_aborts_and_removes_partial_archive(self):
        created_path = None

        def fail_after_writing(archive, *, archive_name: str, **_kwargs):
            nonlocal created_path
            created_path = archive.filename
            archive.writestr(archive_name, b"partial")
            raise RuntimeError("object disappeared")

        with (
            patch.object(
                output_service,
                "list_bucket_objects",
                return_value=[{"key": "job1/build.log"}],
            ),
            patch.object(
                output_service,
                "stream_object_to_zip",
                side_effect=fail_after_writing,
            ),
            pytest.raises(RuntimeError, match="object disappeared"),
        ):
            output_service.build_job_output_zip("job1")

        assert created_path is not None
        assert not os.path.exists(created_path)

    def test_no_entries_raises_file_not_found(self):
        with (
            patch.object(output_service, "list_bucket_objects", return_value=[]),
            pytest.raises(FileNotFoundError, match="No output files"),
        ):
            output_service.build_job_output_zip("job1")

    def test_submitted_key_must_belong_to_job(self):
        with pytest.raises(ValueError, match="does not belong"):
            output_service.build_job_output_zip("job1", "another/code.zip")

    def test_unsafe_job_id_raises_value_error_before_store_access(self):
        with (
            patch.object(output_service, "list_bucket_objects") as list_objects,
            pytest.raises(ValueError, match="Invalid job_id"),
        ):
            output_service.build_job_output_zip("../evil")
        list_objects.assert_not_called()

    def test_list_failure_propagates(self):
        with (
            patch.object(
                output_service,
                "list_bucket_objects",
                side_effect=RuntimeError("store down"),
            ),
            pytest.raises(RuntimeError, match="store down"),
        ):
            output_service.build_job_output_zip("job1")

    def test_duplicate_archive_names_are_rejected(self):
        with (
            patch.object(
                output_service,
                "list_bucket_objects",
                return_value=[
                    {"key": "job1/build.log"},
                    {"key": "job1/build.log"},
                ],
            ),
            pytest.raises(RuntimeError, match="Duplicate archive entry"),
        ):
            output_service.build_job_output_zip("job1")


async def _collect_stream(entries, fetcher) -> bytes:
    with patch.object(
        output_service, "_afetch_object_chunks", side_effect=fetcher
    ):
        chunks = []
        async for chunk in output_service.aiter_entries_as_zip(entries):
            chunks.append(chunk)
        return b"".join(chunks)


class TestCollectJobOutputEntries:
    def test_collect_matches_builder_entries(self):
        with patch.object(
            output_service,
            "list_bucket_objects",
            return_value=[
                {"key": "job1/build.log", "size": 8},
                {"key": "job1/nested/model.pt", "size": 12},
                {"key": "job1/../evil"},
                "not-an-object",
            ],
        ):
            entries = output_service.collect_job_output_entries(
                "job1", "job1/code.zip"
            )
        assert entries == [
            ("uploads", "job1/code.zip", "submitted/code.zip", None, True),
            ("outputs", "job1/build.log", "outputs/build.log", 8, False),
            (
                "outputs",
                "job1/nested/model.pt",
                "outputs/nested/model.pt",
                12,
                False,
            ),
        ]

    def test_collect_rejects_unsafe_job_id_without_store_access(self):
        with (
            patch.object(output_service, "list_bucket_objects") as list_objects,
            pytest.raises(ValueError, match="Invalid job_id"),
        ):
            output_service.collect_job_output_entries("../evil")
        list_objects.assert_not_called()


class TestStreamEntriesAsZip:
    @pytest.mark.asyncio()
    async def test_streams_valid_zip_readable_by_stdlib(self):
        async def fetcher(client, *, bucket, object_key, size, force_presigned):
            yield b"hello "
            yield f"from:{object_key}".encode()

        entries = [
            ("uploads", "j1/code.zip", "submitted/code.zip", None, True),
            ("outputs", "j1/a/b.pt", "outputs/a/b.pt", 7, False),
        ]
        blob = await _collect_stream(entries, fetcher)
        assert blob[:2] == b"PK"
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            assert set(archive.namelist()) == {
                "submitted/code.zip",
                "outputs/a/b.pt",
            }
            assert archive.read("submitted/code.zip") == b"hello from:j1/code.zip"
            assert archive.read("outputs/a/b.pt") == b"hello from:j1/a/b.pt"

    @pytest.mark.asyncio()
    async def test_first_byte_available_before_objects_finish(self):
        """The local-file header must stream before slow object bytes arrive."""
        started = False

        async def slow_fetcher(client, **kwargs):
            nonlocal started
            started = True
            yield b"slow-bytes"

        entries = [("outputs", "j1/big.pt", "outputs/big.pt", 10, False)]
        with patch.object(
            output_service, "_afetch_object_chunks", side_effect=slow_fetcher
        ):
            stream = output_service.aiter_entries_as_zip(entries)
            first = await stream.__anext__()
            assert first[:4] == b"PK\x03\x04"
            rest = [first]
            async for chunk in stream:
                rest.append(chunk)
        assert started is True
        with zipfile.ZipFile(io.BytesIO(b"".join(rest))) as archive:
            assert archive.read("outputs/big.pt") == b"slow-bytes"

    @pytest.mark.asyncio()
    async def test_empty_object_streams_as_empty_entry(self):
        async def empty_fetcher(client, **kwargs):
            if False:
                yield b"never"

        blob = await _collect_stream(
            [("outputs", "j1/empty.log", "outputs/empty.log", 0, False)],
            empty_fetcher,
        )
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            assert archive.read("outputs/empty.log") == b""

    @pytest.mark.asyncio()
    async def test_mid_stream_object_failure_aborts(self):
        async def failing_fetcher(client, **kwargs):
            yield b"partial"
            raise RuntimeError("object disappeared")

        with (
            patch.object(
                output_service, "_afetch_object_chunks", side_effect=failing_fetcher
            ),
            pytest.raises(RuntimeError, match="Failed to download object"),
        ):
            async for _ in output_service.aiter_entries_as_zip(
                [("outputs", "j1/a.log", "outputs/a.log", 7, False)]
            ):
                pass

    @pytest.mark.asyncio()
    async def test_large_multi_chunk_binary_roundtrip(self):
        payload = os.urandom(3 * (1 << 20) + 13)

        async def chunked_fetcher(client, **kwargs):
            for offset in range(0, len(payload), 1 << 20):
                yield payload[offset : offset + (1 << 20)]

        blob = await _collect_stream(
            [("outputs", "j1/model.pt", "outputs/model.pt", len(payload), False)],
            chunked_fetcher,
        )
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            assert archive.read("outputs/model.pt") == payload
