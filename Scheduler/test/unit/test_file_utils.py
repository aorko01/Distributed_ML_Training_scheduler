"""Unit tests for app/utils/file_utils.py."""
import io
import zipfile
from unittest.mock import MagicMock, patch

import pytest

from app.utils import file_utils
from app.utils.file_utils import (
    find_file_in_zip,
    save_to_object_store,
    validate_required_files,
)


def _make_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


class TestFindFileInZip:
    def test_top_level_match(self):
        assert find_file_in_zip(["requirements.txt", "train.py"], "requirements.txt") == (
            "requirements.txt"
        )

    def test_nested_basename_match(self):
        assert (
            find_file_in_zip(["proj/requirements.txt", "proj/train.py"], "requirements.txt")
            == "proj/requirements.txt"
        )

    def test_first_match_wins(self):
        names = ["a/train.py", "b/train.py"]
        assert find_file_in_zip(names, "train.py") == "a/train.py"

    def test_directory_entries_skipped(self):
        assert find_file_in_zip(["mydir/", "other.txt"], "mydir") is None

    def test_no_match_returns_none(self):
        assert find_file_in_zip(["a.py", "b.py"], "requirements.txt") is None

    def test_empty_list_returns_none(self):
        assert find_file_in_zip([], "requirements.txt") is None


class TestValidateRequiredFiles:
    def test_all_present_passes(self):
        validate_required_files(["a/requirements.txt", "train.py"], ["requirements.txt"])

    def test_missing_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError, match="requirements.txt"):
            validate_required_files(["train.py"], ["requirements.txt"])

    def test_empty_requirements_passes(self):
        validate_required_files(["anything.py"], [])


class TestSaveToObjectStore:
    def _ok_response(self, object_key):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"object_key": object_key}
        return resp

    def test_success_returns_object_key_and_matched_files(self):
        content = _make_zip(
            {"myjob/train.py": "x", "myjob/requirements.txt": "torch"}
        )
        with patch.object(
            file_utils.requests, "post", return_value=self._ok_response("jid/myjob.zip")
        ) as mock_post:
            result = save_to_object_store(
                content, "myjob.zip", require_files=["requirements.txt"], job_id="jid"
            )
        assert result["object_key"] == "jid/myjob.zip"
        assert "myjob/train.py" in result["files"]
        _, kwargs = mock_post.call_args
        assert kwargs["data"]["object_key"] == "jid/myjob.zip"
        assert kwargs["data"]["bucket"] == file_utils.OBJECT_STORE_BUCKET

    def test_generates_job_id_and_filename_when_missing(self):
        content = _make_zip({"requirements.txt": "torch"})
        with patch.object(
            file_utils.requests, "post", return_value=self._ok_response("x/y.zip")
        ):
            result = save_to_object_store(content, "", job_id=None)
        assert result["object_key"] == "x/y.zip"

    def test_missing_required_file_raises(self):
        content = _make_zip({"train.py": "x"})
        with pytest.raises(FileNotFoundError):
            save_to_object_store(
                content, "job.zip", require_files=["requirements.txt"], job_id="j1"
            )

    def test_invalid_zip_raises_bad_zip(self):
        with pytest.raises(zipfile.BadZipFile):
            save_to_object_store(b"not a zip", "job.zip", job_id="j1")

    def test_object_store_error_raises_runtime_error(self):
        content = _make_zip({"requirements.txt": "torch"})
        resp = MagicMock()
        resp.status_code = 500
        resp.text = "boom"
        with patch.object(file_utils.requests, "post", return_value=resp):
            with pytest.raises(RuntimeError, match="Object store upload failed"):
                save_to_object_store(content, "job.zip", job_id="j1")

    def test_uses_returned_object_key_when_present(self):
        content = _make_zip({"requirements.txt": "torch"})
        with patch.object(
            file_utils.requests, "post", return_value=self._ok_response("server/key.zip")
        ):
            result = save_to_object_store(content, "job.zip", job_id="j1")
        assert result["object_key"] == "server/key.zip"

    def test_falls_back_to_local_key_when_response_has_no_key(self):
        content = _make_zip({"requirements.txt": "torch"})
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {}
        with patch.object(file_utils.requests, "post", return_value=resp):
            result = save_to_object_store(content, "job.zip", job_id="j1")
        assert result["object_key"] == "j1/job.zip"
