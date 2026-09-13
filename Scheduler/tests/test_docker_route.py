"""Unit tests for app/api/docker_route.py."""
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.docker_route import (
    _fetch_all_tags,
    _parse_runtime_tags,
    _sort_key,
    get_pytorch_tags,
)


class TestSortKey:
    def test_simple_version(self):
        assert _sort_key("2.1") == (2, 1)

    def test_three_parts(self):
        assert _sort_key("2.1.0") == (2, 1, 0)

    def test_numeric_not_lexicographic(self):
        assert _sort_key("2.10") > _sort_key("2.9")

    def test_non_numeric_falls_back_to_zeros(self):
        assert _sort_key("abc") == (0,)
        assert _sort_key("1.x") == (0, 0)


class TestParseRuntimeTags:
    def test_groups_by_pytorch_version(self):
        tags = [
            "2.1-cuda11.8-cudnn8-runtime",
            "2.1-cuda12.1-cudnn8-runtime",
            "2.0-cuda11.8-cudnn8-runtime",
        ]
        result = _parse_runtime_tags(tags)
        assert [r["version"] for r in result] == ["2.1", "2.0"]
        assert len(result[0]["cudaVersions"]) == 2

    def test_non_runtime_tags_filtered(self):
        tags = ["latest", "2.1-cuda11.8-cudnn8-devel", "2.1-cuda11.8-cudnn8-runtime"]
        result = _parse_runtime_tags(tags)
        assert len(result) == 1
        assert result[0]["version"] == "2.1"

    def test_versions_sorted_descending(self):
        tags = [
            "1.13-cuda11.6-cudnn8-runtime",
            "2.1-cuda12.1-cudnn8-runtime",
            "2.10-cuda12.1-cudnn8-runtime",
        ]
        result = _parse_runtime_tags(tags)
        assert [r["version"] for r in result] == ["2.10", "2.1", "1.13"]

    def test_cuda_variants_sorted_descending(self):
        tags = [
            "2.1-cuda11.8-cudnn8-runtime",
            "2.1-cuda12.1-cudnn8-runtime",
        ]
        result = _parse_runtime_tags(tags)
        assert result[0]["cudaVersions"][0]["cuda"] == "12.1"

    def test_tag_field_is_full_image_reference(self):
        result = _parse_runtime_tags(["2.1-cuda11.8-cudnn8-runtime"])
        assert result[0]["cudaVersions"][0]["tag"] == (
            "pytorch/pytorch:2.1-cuda11.8-cudnn8-runtime"
        )

    def test_empty_input_returns_empty(self):
        assert _parse_runtime_tags([]) == []


class TestFetchAllTags:
    def _resp(self, names, next_url=None):
        resp = MagicMock()
        resp.json.return_value = {
            "results": [{"name": n} for n in names],
            "next": next_url,
        }
        return resp

    def test_single_page(self):
        with patch(
            "app.api.docker_route.requests.get",
            return_value=self._resp(["a", "b"]),
        ) as mock_get:
            assert _fetch_all_tags() == ["a", "b"]
            mock_get.assert_called_once()

    def test_pagination_followed_until_none(self):
        first = self._resp(["a"], next_url="http://x/page2")
        second = self._resp(["b"], next_url=None)
        with patch(
            "app.api.docker_route.requests.get", side_effect=[first, second]
        ):
            assert _fetch_all_tags() == ["a", "b"]

    def test_http_error_propagates(self):
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("boom")
        with patch("app.api.docker_route.requests.get", return_value=resp):
            with pytest.raises(Exception):
                _fetch_all_tags()


class TestGetPytorchTagsEndpoint:
    def test_success(self):
        with (
            patch(
                "app.api.docker_route._fetch_all_tags",
                return_value=["2.1-cuda11.8-cudnn8-runtime"],
            ),
        ):
            result = get_pytorch_tags()
        assert result[0]["version"] == "2.1"

    def test_failure_raises_502(self):
        with patch(
            "app.api.docker_route._fetch_all_tags",
            side_effect=Exception("hub down"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                get_pytorch_tags()
        assert exc_info.value.status_code == 502
