"""Unit tests for database.py (SQLite idempotency + base-image LRU)."""
import os

import pytest

import database


class TestInitDb:
    def test_creates_tables_and_is_idempotent(self, temp_db):
        database.init_db()  # second call must not fail
        assert database.is_job_processed("nothing") is False
        assert database.get_old_base_images() == []

    def test_creates_parent_dirs(self, tmp_path, monkeypatch):
        nested = str(tmp_path / "a" / "b" / "builder.db")
        monkeypatch.setattr(database, "DB_PATH", nested)
        database.init_db()
        assert os.path.exists(nested)


class TestProcessedJobs:
    def test_mark_and_check(self, temp_db):
        assert database.is_job_processed("j1") is False
        database.mark_job_processed("j1")
        assert database.is_job_processed("j1") is True

    def test_mark_is_upsert(self, temp_db):
        database.mark_job_processed("j1")
        database.mark_job_processed("j1")
        assert database.is_job_processed("j1") is True

    def test_independent_job_ids(self, temp_db):
        database.mark_job_processed("j1")
        assert database.is_job_processed("j2") is False


class TestBaseImages:
    def test_old_images_returned_after_cutoff(self, temp_db):
        database.update_base_image_usage("img:new")
        assert database.get_old_base_images(days=7) == []
        # days=0 -> cutoff ~= now; the just-written row may be == cutoff.
        # Use a negative window to guarantee it is older than the cutoff.
        assert database.get_old_base_images(days=-1) == ["img:new"]

    def test_recent_images_not_old(self, temp_db):
        database.update_base_image_usage("img:fresh")
        assert database.get_old_base_images(days=365) == []

    def test_remove_record(self, temp_db):
        database.update_base_image_usage("img:gone")
        database.remove_base_image_record("img:gone")
        assert database.get_old_base_images(days=-1) == []

    def test_remove_missing_is_noop(self, temp_db):
        database.remove_base_image_record("img:missing")

    def test_update_is_upsert(self, temp_db):
        database.update_base_image_usage("img:x")
        database.update_base_image_usage("img:x")
        assert database.get_old_base_images(days=-1) == ["img:x"]
