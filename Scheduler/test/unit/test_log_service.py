"""Unit tests for app/services/log_service.py."""
from unittest.mock import MagicMock, patch

import pytest

from app.services import log_service


class TestPublishLogLines:
    @pytest.mark.asyncio()
    async def test_empty_lines_no_redis_call(self, fake_redis):
        with patch.object(log_service, "redis_client", fake_redis):
            await log_service.publish_log_lines("job1", [])
        assert fake_redis.streams == {}

    @pytest.mark.asyncio()
    async def test_lines_written_to_stream(self, fake_redis):
        with patch.object(log_service, "redis_client", fake_redis):
            await log_service.publish_log_lines("job1", ["a", "b"])
        entries = fake_redis.streams["logs:job1"]
        assert [e[1]["line"] for e in entries] == ["a", "b"]
        assert all("ts" in e[1] for e in entries)


class TestGetLogStreamHistory:
    @pytest.mark.asyncio()
    async def test_returns_parsed_entries(self, fake_redis):
        fake_redis.streams["logs:j"] = [
            ("1-0", {"line": "hello", "ts": "123"}),
            ("2-0", {"line": "world", "ts": "456"}),
        ]
        with patch.object(log_service, "redis_client", fake_redis):
            out = await log_service.get_log_stream_history("j")
        assert out == [
            {"id": "1-0", "line": "hello", "ts": 123},
            {"id": "2-0", "line": "world", "ts": 456},
        ]

    @pytest.mark.asyncio()
    async def test_missing_keys_default(self, fake_redis):
        fake_redis.streams["logs:j"] = [("1-0", {})]
        with patch.object(log_service, "redis_client", fake_redis):
            out = await log_service.get_log_stream_history("j")
        assert out == [{"id": "1-0", "line": "", "ts": 0}]


class TestReadLogStream:
    @pytest.mark.asyncio()
    async def test_reads_new_entries(self, fake_redis):
        fake_redis.streams["logs:j"] = [
            ("1-0", {"line": "a", "ts": "1"}),
            ("2-0", {"line": "b", "ts": "2"}),
        ]
        with patch.object(log_service, "redis_client", fake_redis):
            out = await log_service.read_log_stream("j", "1-0")
        assert [m["id"] for m in out] == ["2-0"]


class TestFetchBuildLog:
    def test_success_returns_text(self):
        resp = MagicMock(status_code=200, text="log-content")
        with patch.object(log_service.requests, "get", return_value=resp):
            assert log_service.fetch_build_log_from_object_store("j") == "log-content"

    def test_404_returns_empty(self):
        resp = MagicMock(status_code=404)
        with patch.object(log_service.requests, "get", return_value=resp):
            assert log_service.fetch_build_log_from_object_store("j") == ""

    def test_exception_returns_empty(self):
        with patch.object(
            log_service.requests, "get", side_effect=Exception("down")
        ):
            assert log_service.fetch_build_log_from_object_store("j") == ""


class TestSplitStreams:
    @pytest.mark.asyncio()
    async def test_build_and_training_streams_are_separate(self, fake_redis):
        with patch.object(log_service, "redis_client", fake_redis):
            await log_service.publish_log_lines("job1", ["build line"], stream="build")
            await log_service.publish_log_lines("job1", ["train line"])
        assert [e[1]["line"] for e in fake_redis.streams["build_logs:job1"]] == ["build line"]
        assert [e[1]["line"] for e in fake_redis.streams["logs:job1"]] == ["train line"]

    @pytest.mark.asyncio()
    async def test_build_history_reads_build_stream(self, fake_redis):
        fake_redis.streams["build_logs:j"] = [("1-0", {"line": "b", "ts": "1"})]
        fake_redis.streams["logs:j"] = [("1-0", {"line": "t", "ts": "1"})]
        with patch.object(log_service, "redis_client", fake_redis):
            out = await log_service.get_log_stream_history("j", stream="build")
        assert out == [{"id": "1-0", "line": "b", "ts": 1}]

    def test_training_log_prefers_training_object(self):
        calls = []

        def _fake_get(url, timeout=30):
            calls.append(url)
            resp = MagicMock(status_code=200, text="training-content")
            return resp

        with patch.object(log_service.requests, "get", side_effect=_fake_get):
            assert log_service.fetch_training_log_from_object_store("j") == "training-content"
        assert calls[0].endswith("/j/training.log")

    def test_training_log_falls_back_to_legacy_build_log(self):
        def _fake_get(url, timeout=30):
            if url.endswith("/training.log"):
                return MagicMock(status_code=404)
            return MagicMock(status_code=200, text="legacy-mixed")

        with patch.object(log_service.requests, "get", side_effect=_fake_get):
            assert log_service.fetch_training_log_from_object_store("j") == "legacy-mixed"
