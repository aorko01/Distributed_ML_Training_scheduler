"""Unit tests for Scheduler/app/utils/file_utils.py."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from app.utils.file_utils import find_file_in_zip, validate_required_files  # noqa: E402


class TestFindFileInZip:
    def test_exact_match(self):
        names = ["model.py", "train.py", "requirements.txt"]
        assert find_file_in_zip(names, "train.py") == "train.py"

    def test_nested_path_match(self):
        names = ["src/model.py", "src/train.py", "data/train.py"]
        assert find_file_in_zip(names, "train.py") == "src/train.py"

    def test_no_match(self):
        names = ["model.py", "requirements.txt"]
        assert find_file_in_zip(names, "train.py") is None

    def test_skips_directories(self):
        names = ["src/", "src/model.py", "data/", "train.py"]
        assert find_file_in_zip(names, "train.py") == "train.py"

    def test_empty_list(self):
        assert find_file_in_zip([], "train.py") is None

    def test_directory_entry_not_returned(self):
        names = ["train.py/"]
        assert find_file_in_zip(names, "train.py") is None


class TestValidateRequiredFiles:
    def test_all_files_present(self):
        names = ["train.py", "model.py", "requirements.txt"]
        validate_required_files(names, ["train.py", "model.py"])
        # No exception means success

    def test_missing_file_raises(self):
        names = ["train.py"]
        with pytest.raises(FileNotFoundError) as exc_info:
            validate_required_files(names, ["train.py", "model.py"])
        assert "model.py" in str(exc_info.value)

    def test_multiple_missing_files(self):
        names = ["train.py"]
        with pytest.raises(FileNotFoundError) as exc_info:
            validate_required_files(names, ["model.py", "config.yaml"])
        assert "model.py" in str(exc_info.value)

    def test_empty_required_list(self):
        names = ["train.py"]
        validate_required_files(names, [])

    def test_nested_paths(self):
        names = ["src/train.py", "config/train.yaml"]
        validate_required_files(names, ["train.py", "train.yaml"])
