"""Unit tests for Docker_Image_Builder/database.py — SQLite operations."""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from database import (  # noqa: E402
    init_db,
    is_job_processed,
    mark_job_processed,
    update_base_image_usage,
    get_old_base_images,
    remove_base_image_record,
    get_connection,
)


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """Use a temporary SQLite database for each test."""
    db_path = str(tmp_path / "test_builder.db")
    monkeypatch.setattr("database.DB_PATH", db_path)
    init_db()


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------
class TestInitDb:
    def test_creates_tables(self):
        conn = get_connection()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {t[0] for t in tables}
        assert "processed_jobs" in table_names
        assert "base_images" in table_names

    def test_idempotent(self):
        init_db()
        init_db()
        conn = get_connection()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        assert len(tables) >= 2


# ---------------------------------------------------------------------------
# is_job_processed / mark_job_processed
# ---------------------------------------------------------------------------
class TestJobProcessing:
    def test_job_not_processed(self):
        assert is_job_processed("job-123") is False

    def test_mark_and_check(self):
        mark_job_processed("job-123")
        assert is_job_processed("job-123") is True

    def test_mark_idempotent(self):
        mark_job_processed("job-123")
        mark_job_processed("job-123")
        assert is_job_processed("job-123") is True

    def test_different_jobs(self):
        mark_job_processed("job-1")
        assert is_job_processed("job-1") is True
        assert is_job_processed("job-2") is False


# ---------------------------------------------------------------------------
# update_base_image_usage / get_old_base_images / remove_base_image_record
# ---------------------------------------------------------------------------
class TestBaseImageTracking:
    def test_update_creates_record(self):
        update_base_image_usage("python:3.11")
        old = get_old_base_images(days=7)
        # Should NOT be old (just updated)
        assert "python:3.11" not in old

    def test_old_image_detected(self, monkeypatch):
        # Insert an image with a timestamp 10 days ago
        conn = get_connection()
        old_date = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        conn.execute(
            "INSERT INTO base_images (image_name, last_used_at) VALUES (?, ?)",
            ("old-image:latest", old_date),
        )
        conn.commit()
        old = get_old_base_images(days=7)
        assert "old-image:latest" in old

    def test_new_image_not_detected_as_old(self, monkeypatch):
        conn = get_connection()
        new_date = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO base_images (image_name, last_used_at) VALUES (?, ?)",
            ("new-image:latest", new_date),
        )
        conn.commit()
        old = get_old_base_images(days=7)
        assert "new-image:latest" not in old

    def test_remove_record(self):
        conn = get_connection()
        conn.execute(
            "INSERT INTO base_images (image_name, last_used_at) VALUES (?, ?)",
            ("to-remove:latest", datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        remove_base_image_record("to-remove:latest")
        result = conn.execute(
            "SELECT 1 FROM base_images WHERE image_name = ?", ("to-remove:latest",)
        ).fetchone()
        assert result is None

    def test_remove_nonexistent_is_safe(self):
        remove_base_image_record("does-not-exist:latest")

    def test_update_replaces_existing(self):
        update_base_image_usage("myimage:v1")
        update_base_image_usage("myimage:v2")
        conn = get_connection()
        count = conn.execute(
            "SELECT COUNT(*) FROM base_images WHERE image_name LIKE 'myimage%'"
        ).fetchone()[0]
        assert count == 2


# ---------------------------------------------------------------------------
# get_connection — WAL mode
# ---------------------------------------------------------------------------
class TestGetConnection:
    def test_returns_wal_mode(self):
        conn = get_connection()
        try:
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert journal_mode == "wal"
        finally:
            conn.close()
