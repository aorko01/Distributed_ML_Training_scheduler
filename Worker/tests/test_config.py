"""Unit tests for config.py (scheduler URL state + .env persistence)."""
import os

import pytest

import config


class TestSchedulerUrl:
    def test_get_returns_env_url(self):
        assert config.get_scheduler_url().startswith("http")

    def test_set_strips_slashes(self, restore_scheduler_url):
        config.set_scheduler_url("http://example.com///")
        assert config.get_scheduler_url() == "http://example.com"

    def test_set_whitespace_keeps_old(self, restore_scheduler_url):
        before = config.get_scheduler_url()
        config.set_scheduler_url("   ")
        assert config.get_scheduler_url() == before

    def test_set_empty_keeps_old(self, restore_scheduler_url):
        before = config.get_scheduler_url()
        config.set_scheduler_url("")
        assert config.get_scheduler_url() == before


class TestPersistEnv:
    @pytest.fixture()
    def backup_env_file(self):
        path = os.path.join(os.path.dirname(os.path.abspath(config.__file__)), ".env")
        existed = os.path.exists(path)
        content = open(path).read() if existed else None
        yield path
        if content is not None:
            with open(path, "w") as f:
                f.write(content)
        elif os.path.exists(path) and not existed:
            os.remove(path)

    def test_adds_new_key(self, backup_env_file, restore_scheduler_url):
        config.persist_env({"WORKER_TEST_KEY": "abc123"})
        with open(backup_env_file) as f:
            assert "WORKER_TEST_KEY=abc123" in f.read()

    def test_overwrites_existing_key_once(self, backup_env_file):
        config.persist_env({"WORKER_TEST_KEY": "one"})
        config.persist_env({"WORKER_TEST_KEY": "two"})
        with open(backup_env_file) as f:
            content = f.read()
        assert content.count("WORKER_TEST_KEY=") == 1
        assert "WORKER_TEST_KEY=two" in content

    def test_keeps_unrelated_keys(self, backup_env_file):
        with open(backup_env_file) as f:
            before = f.read()
        config.persist_env({"WORKER_TEST_KEY": "x"})
        with open(backup_env_file) as f:
            after = f.read()
        for line in before.strip().splitlines():
            if line and not line.startswith("WORKER_TEST_KEY="):
                assert line in after


class TestConstants:
    def test_intervals_positive(self):
        assert config.HEARTBEAT_INTERVAL > 0
        assert config.JOB_POLL_INTERVAL > 0

    def test_output_dir_exists(self):
        assert os.path.isdir(config.OUTPUT_DIR)

    def test_threshold_positive_int(self):
        assert isinstance(config.OBJECT_STORE_LARGE_FILE_THRESHOLD, int)
        assert config.OBJECT_STORE_LARGE_FILE_THRESHOLD > 0
