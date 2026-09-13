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
    def env_file(self, tmp_path, monkeypatch):
        """Redirect persist_env() at an isolated tmp .env.

        persist_env() derives its path from config.__file__, so pointing
        that at a tmp dir keeps the real Worker/.env untouched (it may not
        even exist, e.g. on CI where .env is gitignored).
        """
        fake_module = tmp_path / "config.py"
        fake_module.touch()
        monkeypatch.setattr(config, "__file__", str(fake_module))
        return os.path.join(str(tmp_path), ".env")

    def test_adds_new_key(self, env_file):
        config.persist_env({"WORKER_TEST_KEY": "abc123"})
        with open(env_file) as f:
            assert "WORKER_TEST_KEY=abc123" in f.read()

    def test_overwrites_existing_key_once(self, env_file):
        config.persist_env({"WORKER_TEST_KEY": "one"})
        config.persist_env({"WORKER_TEST_KEY": "two"})
        with open(env_file) as f:
            content = f.read()
        assert content.count("WORKER_TEST_KEY=") == 1
        assert "WORKER_TEST_KEY=two" in content

    def test_keeps_unrelated_keys(self, env_file):
        with open(env_file, "w") as f:
            f.write("UNRELATED_KEY=keepme\nSCHEDULER_URL=http://x\n")
        config.persist_env({"WORKER_TEST_KEY": "x"})
        with open(env_file) as f:
            after = f.read()
        assert "UNRELATED_KEY=keepme" in after
        assert "SCHEDULER_URL=http://x" in after
        assert "WORKER_TEST_KEY=x" in after


class TestConstants:
    def test_intervals_positive(self):
        assert config.HEARTBEAT_INTERVAL > 0
        assert config.JOB_POLL_INTERVAL > 0

    def test_output_dir_exists(self):
        assert os.path.isdir(config.OUTPUT_DIR)

    def test_threshold_positive_int(self):
        assert isinstance(config.OBJECT_STORE_LARGE_FILE_THRESHOLD, int)
        assert config.OBJECT_STORE_LARGE_FILE_THRESHOLD > 0
