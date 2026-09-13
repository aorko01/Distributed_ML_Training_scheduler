"""Unit tests for object_store.py (uploads/downloads with retry + presigned URLs)."""
import io
from unittest.mock import MagicMock, mock_open, patch

import pytest

import object_store
from object_store import ObjectStore, _post_retry


def _resp(status=200, json_body=None, content=b"data"):
    resp = MagicMock()
    resp.status_code = status
    resp.text = "text"
    resp.content = content
    resp.json.return_value = json_body if json_body is not None else {}
    return resp


class TestPostRetry:
    def test_success_first_try(self):
        with (
            patch.object(object_store.requests, "post", return_value=_resp()),
            patch.object(object_store.time, "sleep") as mock_sleep,
        ):
            assert _post_retry("http://x", data={}, files={}) is True
            mock_sleep.assert_not_called()

    def test_retries_then_succeeds(self):
        with (
            patch.object(
                object_store.requests, "post",
                side_effect=[Exception("t1"), Exception("t2"), _resp()],
            ),
            patch.object(object_store.time, "sleep") as mock_sleep,
        ):
            assert _post_retry("http://x", data={}, files={}, retries=3) is True
            assert mock_sleep.call_count == 2

    def test_all_fail_returns_false(self):
        with (
            patch.object(object_store.requests, "post", side_effect=Exception("down")),
            patch.object(object_store.time, "sleep"),
        ):
            assert _post_retry("http://x", data={}, files={}, retries=2) is False


class TestInit:
    def test_defaults_and_overrides(self):
        store = ObjectStore()
        assert store.base_url == object_store.OBJECT_STORE_URL
        custom = ObjectStore(base_url="http://x///", bucket="b")
        assert custom.base_url == "http://x"
        assert custom.bucket == "b"


class TestUploadBytes:
    def test_delegates_to_post_retry(self):
        store = ObjectStore(base_url="http://s", bucket="b")
        with patch.object(object_store, "_post_retry", return_value=True) as mock_post:
            assert store.upload_bytes("j/f.txt", b"hi", "text/plain") is True
        _, kwargs = mock_post.call_args
        assert kwargs["data"] == {"bucket": "b", "object_key": "j/f.txt"}


class TestUploadFile:
    def test_small_file_uploads(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_bytes(b"hello")
        store = ObjectStore(base_url="http://s", bucket="b")
        with (
            patch.object(object_store, "OBJECT_STORE_LARGE_FILE_THRESHOLD", 10**9),
            patch.object(object_store, "_post_retry", return_value=True) as mock_post,
        ):
            assert store.upload_file("j/a.txt", str(f)) is True
        assert mock_post.called

    def test_missing_file_returns_false(self):
        store = ObjectStore()
        assert store.upload_file("j/a.txt", "/nonexistent/file.txt") is False

    def test_large_file_uses_presigned(self, tmp_path):
        f = tmp_path / "big.bin"
        f.write_bytes(b"x" * 100)
        store = ObjectStore(base_url="http://s", bucket="b")
        with (
            patch.object(object_store, "OBJECT_STORE_LARGE_FILE_THRESHOLD", 10),
            patch.object(store, "_upload_large", return_value=True) as mock_large,
        ):
            assert store.upload_file("j/big.bin", str(f)) is True
        mock_large.assert_called_once()


class TestPresignUpload:
    def test_returns_url(self):
        store = ObjectStore(base_url="http://s", bucket="b")
        with patch.object(
            object_store.requests, "post",
            return_value=_resp(json_body={"url": "http://presigned"}),
        ):
            assert store._presign_upload("j/f") == "http://presigned"

    def test_missing_url_raises(self):
        store = ObjectStore(base_url="http://s", bucket="b")
        with patch.object(
            object_store.requests, "post", return_value=_resp(json_body={})
        ):
            with pytest.raises(ValueError):
                store._presign_upload("j/f")


class TestUploadLarge:
    def test_success(self, tmp_path):
        f = tmp_path / "big.bin"
        f.write_bytes(b"data")
        store = ObjectStore(base_url="http://s", bucket="b")
        with (
            patch.object(store, "_presign_upload", return_value="http://up"),
            patch.object(object_store.requests, "put", return_value=_resp()),
            patch.object(object_store.time, "sleep"),
        ):
            assert store._upload_large("j/big.bin", str(f)) is True

    def test_all_fail_returns_false(self, tmp_path):
        f = tmp_path / "big.bin"
        f.write_bytes(b"data")
        store = ObjectStore(base_url="http://s", bucket="b")
        with (
            patch.object(store, "_presign_upload", side_effect=Exception("no url")),
            patch.object(object_store.time, "sleep"),
        ):
            assert store._upload_large("j/big.bin", str(f)) is False


class TestDownload:
    def test_success(self):
        store = ObjectStore(base_url="http://s", bucket="b")
        with patch.object(
            object_store.requests, "get", return_value=_resp(content=b"bytes")
        ):
            assert store.download("j/f") == b"bytes"

    def test_404_returns_none(self):
        store = ObjectStore(base_url="http://s", bucket="b")
        with patch.object(object_store.requests, "get", return_value=_resp(404)):
            assert store.download("j/f") is None

    def test_exception_returns_none(self):
        store = ObjectStore(base_url="http://s", bucket="b")
        with patch.object(object_store.requests, "get", side_effect=Exception("x")):
            assert store.download("j/f") is None


class TestListObjects:
    def test_returns_objects(self):
        store = ObjectStore(base_url="http://s", bucket="b")
        with patch.object(
            object_store.requests, "get",
            return_value=_resp(json_body={"objects": [{"key": "j/a"}]}),
        ) as mock_get:
            out = store.list_objects(prefix="j/")
        assert out == [{"key": "j/a"}]
        assert mock_get.call_args[1]["params"] == {"bucket": "b", "prefix": "j/"}

    def test_exception_returns_empty(self):
        store = ObjectStore(base_url="http://s", bucket="b")
        with patch.object(object_store.requests, "get", side_effect=Exception("x")):
            assert store.list_objects() == []


class TestDownloadTo:
    def test_small_download_writes_file(self, tmp_path):
        store = ObjectStore(base_url="http://s", bucket="b")
        dest = str(tmp_path / "sub" / "f.txt")
        with patch.object(store, "download", return_value=b"content"):
            assert store.download_to("j/f.txt", dest) is True
        with open(dest, "rb") as f:
            assert f.read() == b"content"

    def test_missing_content_returns_false(self, tmp_path):
        store = ObjectStore(base_url="http://s", bucket="b")
        with patch.object(store, "download", return_value=None):
            assert store.download_to("j/f", str(tmp_path / "f")) is False

    def test_large_download_via_presigned(self, tmp_path):
        store = ObjectStore(base_url="http://s", bucket="b")
        dest = str(tmp_path / "big.bin")
        chunks = [b"ab", b"cd"]
        resp = MagicMock()
        resp.iter_content.return_value = chunks
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        with (
            patch.object(object_store, "OBJECT_STORE_LARGE_FILE_THRESHOLD", 2),
            patch.object(store, "_presign_download", return_value="http://dl"),
            patch.object(object_store.requests, "get", return_value=resp),
        ):
            assert store.download_to("j/big.bin", dest, size=100) is True
        with open(dest, "rb") as f:
            assert f.read() == b"abcd"

    def test_large_download_failure_returns_false(self, tmp_path):
        store = ObjectStore(base_url="http://s", bucket="b")
        with (
            patch.object(object_store, "OBJECT_STORE_LARGE_FILE_THRESHOLD", 2),
            patch.object(store, "_presign_download", side_effect=Exception("x")),
        ):
            assert store.download_to("j/b", str(tmp_path / "b"), size=100) is False
