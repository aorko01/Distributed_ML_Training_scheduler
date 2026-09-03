"""Unit tests for Scheduler/app/api/docker_route.py pure functions."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.api.docker_route import _sort_key, _parse_runtime_tags  # noqa: E402


class TestSortKey:
    def test_simple_version(self):
        assert _sort_key("2.0.0") == (2, 0, 0)

    def test_single_number(self):
        assert _sort_key("5") == (5,)

    def test_two_part_version(self):
        assert _sort_key("1.10") == (1, 10)

    def test_non_numeric_falls_back_to_zeros(self):
        result = _sort_key("abc")
        assert result == (0,)

    def test_mixed_numeric_non_numeric(self):
        result = _sort_key("1.abc.3")
        assert result == (0, 0, 0)

    def test_empty_string(self):
        result = _sort_key("")
        assert result == (0,)

    def test_version_ordering(self):
        assert _sort_key("1.10.0") > _sort_key("1.9.0")


class TestParseRuntimeTags:
    def test_valid_tags_parsed(self):
        tags = [
            "2.0.0-cuda11.8-cudnn8-runtime",
            "1.13.0-cuda11.7-cudnn8-runtime",
        ]
        result = _parse_runtime_tags(tags)
        assert len(result) == 2
        assert result[0]["version"] == "2.0.0"
        assert result[1]["version"] == "1.13.0"

    def test_tags_sorted_by_version_desc(self):
        tags = [
            "1.13.0-cuda11.7-cudnn8-runtime",
            "2.0.0-cuda11.8-cudnn8-runtime",
            "1.12.0-cuda11.6-cudnn8-runtime",
        ]
        result = _parse_runtime_tags(tags)
        versions = [r["version"] for r in result]
        assert versions == ["2.0.0", "1.13.0", "1.12.0"]

    def test_cuda_versions_sorted_desc(self):
        tags = [
            "2.0.0-cuda11.7-cudnn8-runtime",
            "2.0.0-cuda11.8-cudnn8-runtime",
        ]
        result = _parse_runtime_tags(tags)
        assert len(result) == 1
        cuda_versions = [v["cuda"] for v in result[0]["cudaVersions"]]
        assert cuda_versions == ["11.8", "11.7"]

    def test_non_runtime_tags_filtered(self):
        tags = [
            "2.0.0-cuda11.8-cudnn8-runtime",
            "2.0.0-cuda11.8-cudnn8-devel",
            "latest",
            "2.0.0-cuda11.8-cudnn8",
        ]
        result = _parse_runtime_tags(tags)
        assert len(result) == 1
        assert result[0]["version"] == "2.0.0"

    def test_empty_tags_list(self):
        result = _parse_runtime_tags([])
        assert result == []

    def test_no_valid_tags(self):
        result = _parse_runtime_tags(["latest", "devel", "base"])
        assert result == []

    def test_tag_format_in_result(self):
        tags = ["2.0.0-cuda11.8-cudnn8-runtime"]
        result = _parse_runtime_tags(tags)
        assert result[0]["cudaVersions"][0]["tag"] == "pytorch/pytorch:2.0.0-cuda11.8-cudnn8-runtime"

    def test_multiple_cudnn_versions(self):
        tags = [
            "2.0.0-cuda11.8-cudnn8-runtime",
            "2.0.0-cuda11.8-cudnn9-runtime",
        ]
        result = _parse_runtime_tags(tags)
        assert len(result) == 1
        cudnn_versions = [v["cudnn"] for v in result[0]["cudaVersions"]]
        assert cudnn_versions == ["9", "8"]
