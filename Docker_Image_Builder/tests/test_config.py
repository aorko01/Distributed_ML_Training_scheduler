"""Unit tests for config.py (URL building, env parsing)."""
import importlib
import os
from unittest.mock import patch


def _reload_config(env_overrides):
    env = {
        "DOCKER_HUB_USERNAME": "testuser",
        "SCHEDULER_API_URL": "http://scheduler:8000/",
        "OBJECT_STORE_URL": "http://store:8010/",
        "OBJECT_STORE_BUCKET": "uploads",
        "OBJECT_OUTPUT_BUCKET": "outputs",
        "DOCKER_HUB_PASSWORD": "",
        "POLL_INTERVAL": "10",
        "DB_PATH": "/data/builder.db",
        "DEBUG_SAVE_LOCAL": "false",
        "DEBUG_LOCAL_DIR": "./debug_jobs",
    }
    env.update(env_overrides)
    with patch.dict(os.environ, env, clear=False):
        # ensure required keys exist even if the ambient env lacks them
        for key, value in env.items():
            os.environ[key] = value
        import config

        return importlib.reload(config)


class TestSchedulerUrls:
    def test_trailing_slash_stripped_once(self):
        config = _reload_config({"SCHEDULER_API_URL": "http://h:8000///"})
        assert config.SCHEDULER_QUEUE_URL == (
            "http://h:8000/jobs/unbuilt_jobs"
        )
        assert config.SCHEDULER_UPDATE_URL.endswith(
            "/jobs/update_job_to_vram_estimation_pending"
        )
        assert config.SCHEDULER_INTERACTIVE_UPDATE_URL.endswith(
            "/jobs/mark_interactive_ready"
        )
        assert config.SCHEDULER_FAILURE_URL.endswith("/jobs/mark_failed")
        assert config.SCHEDULER_LOG_URL.endswith("/jobs/logs")

    def test_default_scheduler_url(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SCHEDULER_API_URL", None)
            os.environ["DOCKER_HUB_USERNAME"] = "u"
            import config

            reloaded = importlib.reload(config)
        assert reloaded.SCHEDULER_BASE_URL == "http://localhost:8000"


class TestSettings:
    def test_poll_interval_parsed(self):
        assert _reload_config({"POLL_INTERVAL": "42"}).POLL_INTERVAL == 42

    def test_debug_flag_truthy_values(self):
        for truthy in ("1", "true", "yes", "on", "TRUE", " Yes "):
            assert _reload_config({"DEBUG_SAVE_LOCAL": truthy}).DEBUG_SAVE_LOCAL is True

    def test_debug_flag_falsy_values(self):
        for falsy in ("0", "false", "no", "off", ""):
            assert _reload_config({"DEBUG_SAVE_LOCAL": falsy}).DEBUG_SAVE_LOCAL is False

    def test_object_store_defaults(self):
        config = _reload_config({})
        assert config.OBJECT_STORE_BUCKET == "uploads"
        assert config.OBJECT_OUTPUT_BUCKET == "outputs"

    def test_missing_username_raises_key_error(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DOCKER_HUB_USERNAME", None)
            import config

            try:
                importlib.reload(config)
            except KeyError:
                pass
            else:
                raise AssertionError("expected KeyError")
            finally:
                os.environ["DOCKER_HUB_USERNAME"] = "testuser"
                importlib.reload(config)
