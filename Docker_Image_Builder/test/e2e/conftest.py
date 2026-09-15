"""Shared fixtures for Docker_Image_Builder end-to-end tests.

- Auto-loads real Docker Hub credentials from `.env_test` (same python-dotenv
  convention the service itself uses via ``config.py``) so local runs don't
  need manual ``export``. CI secrets take precedence over the file.
- Registers ``--run-real-dockerhub``: tests marked ``real_dockerhub`` are
  skipped unless explicitly opted in (flag or ``RUN_REAL_DOCKERHUB_E2E=1``),
  keeping slow/rate-limited real-Hub tests out of the default suite.
"""

import os

import pytest

try:
    from dotenv import dotenv_values
except ImportError:  # pragma: no cover - dotenv is a runtime dep
    dotenv_values = None


def _candidate_env_test_paths():
    """Yield likely `.env_test` locations (file may live at builder or repo root)."""
    here = os.path.abspath(os.path.dirname(__file__))
    builder_dir = os.path.abspath(os.path.join(here, os.pardir, os.pardir))
    repo_root = os.path.abspath(os.path.join(builder_dir, os.pardir))
    cwd = os.path.abspath(os.getcwd())
    seen = set()
    for path in (
        os.path.join(cwd, ".env_test"),
        os.path.join(builder_dir, ".env_test"),
        os.path.join(repo_root, ".env_test"),
    ):
        if path not in seen:
            seen.add(path)
            yield path


_PLACEHOLDER_PREFIXES = ("your-", "changeme", "example", "<")


def _is_placeholder(value: str) -> bool:
    lowered = (value or "").strip().lower()
    return not lowered or any(lowered.startswith(p) for p in _PLACEHOLDER_PREFIXES)


# Dummy values installed by test/conftest.py / CI unit jobs — real file values
# should win over these, but an explicitly exported real value always wins.
_DUMMY_USERNAMES = {"", "testuser", "ci-test-user"}


def _load_env_test():
    """Load `.env_test` via python-dotenv without clobbering explicit env vars."""
    if dotenv_values is None:
        return
    for path in _candidate_env_test_paths():
        if not os.path.isfile(path):
            continue
        try:
            values = dotenv_values(path)
        except Exception:
            continue
        for key in ("DOCKER_HUB_USERNAME", "DOCKER_HUB_PASSWORD", "RUN_REAL_DOCKERHUB_E2E"):
            file_val = (values.get(key) or "").strip()
            if not file_val or _is_placeholder(file_val):
                continue
            current = (os.environ.get(key) or "").strip()
            if key == "DOCKER_HUB_USERNAME":
                if current in _DUMMY_USERNAMES or _is_placeholder(current):
                    os.environ[key] = file_val
            elif key == "DOCKER_HUB_PASSWORD":
                # Parent conftest defaults this to "" — a real file value wins,
                # but an explicitly exported non-empty value always wins.
                if not current:
                    os.environ[key] = file_val
            else:  # RUN_REAL_DOCKERHUB_E2E
                os.environ.setdefault(key, file_val)


_load_env_test()


def pytest_addoption(parser):
    parser.addoption(
        "--run-real-dockerhub",
        action="store_true",
        default=False,
        help="Run tests marked real_dockerhub (real Docker Hub login/build/push).",
    )


def _real_dockerhub_opted_in(config) -> bool:
    if config.getoption("--run-real-dockerhub", default=False):
        return True
    return (os.environ.get("RUN_REAL_DOCKERHUB_E2E") or "").strip() in ("1", "true", "yes", "on")


def pytest_collection_modifyitems(config, items):
    # Marker is registered in Docker_Image_Builder/pytest.ini; guard here too
    # so running from another root without the ini still gets a clean skip.
    config.addinivalue_line(
        "markers",
        "real_dockerhub: real Docker Hub end-to-end (excluded from default runs).",
    )
    if _real_dockerhub_opted_in(config):
        return
    skip_marker = pytest.mark.skip(
        reason="real Docker Hub e2e: pass --run-real-dockerhub or set RUN_REAL_DOCKERHUB_E2E=1 to run"
    )
    for item in items:
        if "real_dockerhub" in item.keywords:
            item.add_marker(skip_marker)
