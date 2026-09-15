"""Shared fixtures for Docker_Image_Builder unit tests.

- Sets ``DOCKER_HUB_USERNAME`` *before* any local module is imported
  (``config.py`` raises ``KeyError`` at import time when it is missing).
- Puts the ``Docker_Image_Builder/`` directory on ``sys.path`` so the flat
  ``import config`` / ``import builder`` style imports work from anywhere.
- Redirects the SQLite ``DB_PATH`` at a per-test temp file via fixture.
"""
import os
import sys

import pytest

os.environ.setdefault("DOCKER_HUB_USERNAME", "testuser")
os.environ.setdefault("DOCKER_HUB_PASSWORD", "")
os.environ.setdefault("SCHEDULER_API_URL", "http://localhost:8000")
os.environ.setdefault("OBJECT_STORE_URL", "http://localhost:8010")

BUILDER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BUILDER_DIR not in sys.path:
    sys.path.insert(0, BUILDER_DIR)


@pytest.fixture()
def temp_db(monkeypatch, tmp_path):
    """Point database.DB_PATH at an isolated temp SQLite file."""
    import database

    db_path = str(tmp_path / "builder.db")
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()
    return db_path
