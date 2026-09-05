"""Unit tests for Docker_Image_Builder/builder.py — pure functions."""

import os
import sys
import shutil
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from builder import find_project_dir, extract_job_archive  # noqa: E402


# ---------------------------------------------------------------------------
# find_project_dir
# ---------------------------------------------------------------------------
class TestFindProjectDir:
    def test_returns_first_non_hidden_dir(self, tmp_path):
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / ".git").mkdir()
        (tmp_path / "alpha_project").mkdir()
        (tmp_path / "z_project").mkdir()
        result = find_project_dir(str(tmp_path))
        # sorted() picks alphabetically first: alpha_project
        assert result == str(tmp_path / "alpha_project")

    def test_returns_root_if_no_dirs(self, tmp_path):
        (tmp_path / "file.txt").write_text("hello")
        result = find_project_dir(str(tmp_path))
        assert result == str(tmp_path)

    def test_returns_root_if_only_hidden_dirs(self, tmp_path):
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / ".git").mkdir()
        result = find_project_dir(str(tmp_path))
        assert result == str(tmp_path)

    def test_skips_underscore_prefixed(self, tmp_path):
        (tmp_path / "__init__").mkdir()
        project = tmp_path / "src"
        project.mkdir()
        result = find_project_dir(str(tmp_path))
        assert result == str(project)


# ---------------------------------------------------------------------------
# extract_job_archive
# ---------------------------------------------------------------------------
class TestExtractJobArchive:
    def _make_zip(self, directory: str, files: dict) -> str:
        """Create a zip file containing the given files."""
        zip_path = os.path.join(directory, "test.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            for name, content in files.items():
                zf.writestr(name, content)
        return zip_path

    def test_extracts_files(self, tmp_path):
        zip_path = self._make_zip(str(tmp_path), {
            "train.py": "print('train')",
            "model.py": "print('model')",
        })
        extract_dir = extract_job_archive(zip_path, "test-job")
        try:
            assert os.path.exists(os.path.join(extract_dir, "train.py"))
            assert os.path.exists(os.path.join(extract_dir, "model.py"))
            assert open(os.path.join(extract_dir, "train.py")).read() == "print('train')"
        finally:
            shutil.rmtree(extract_dir)

    def test_extracts_nested_dirs(self, tmp_path):
        zip_path = self._make_zip(str(tmp_path), {
            "src/train.py": "print('nested')",
        })
        extract_dir = extract_job_archive(zip_path, "test-job")
        try:
            assert os.path.exists(os.path.join(extract_dir, "src", "train.py"))
        finally:
            shutil.rmtree(extract_dir)

    def test_creates_temp_dir_with_prefix(self, tmp_path):
        zip_path = self._make_zip(str(tmp_path), {"a.txt": "a"})
        extract_dir = extract_job_archive(zip_path, "abc123")
        try:
            assert "job_abc123_" in extract_dir
            assert os.path.isdir(extract_dir)
        finally:
            shutil.rmtree(extract_dir)

    def test_invalid_zip_raises(self, tmp_path):
        bad_zip = os.path.join(str(tmp_path), "bad.zip")
        with open(bad_zip, "w") as f:
            f.write("not a zip file")
        with pytest.raises(zipfile.BadZipFile):
            extract_job_archive(bad_zip, "test-job")
