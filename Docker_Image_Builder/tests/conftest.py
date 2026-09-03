"""Shared pytest fixtures for Docker_Image_Builder unit tests."""

import os
import sys

# Set required env vars BEFORE any app imports
os.environ.setdefault("DOCKER_HUB_USERNAME", "testuser")
os.environ.setdefault("DOCKER_HUB_PASSWORD", "testpass")
os.environ.setdefault("SCHEDULER_API_URL", "http://localhost:8000")
os.environ.setdefault("OBJECT_STORE_URL", "http://localhost:8010")
os.environ.setdefault("OBJECT_STORE_BUCKET", "uploads")
os.environ.setdefault("OBJECT_OUTPUT_BUCKET", "outputs")

# Ensure the Docker_Image_Builder package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402


@pytest.fixture
def tmp_project_dir(tmp_path):
    """Create a minimal project directory for testing."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "train.py").write_text("print('hello')")
    return str(project)


@pytest.fixture
def tmp_project_dir_with_requirements(tmp_path):
    """Create a project directory with requirements.txt."""
    project = tmp_path / "project_reqs"
    project.mkdir()
    (project / "train.py").write_text("print('hello')")
    (project / "requirements.txt").write_text("numpy\ntorch\n")
    return str(project)


@pytest.fixture
def tmp_build_dir(tmp_path):
    """Create a temporary build directory."""
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    return str(build_dir)


@pytest.fixture
def mock_docker_client():
    """Create a mock Docker client."""
    client = MagicMock()
    client.images.build.return_value = (MagicMock(), iter([]))
    client.images.push.return_value = iter([])
    return client
