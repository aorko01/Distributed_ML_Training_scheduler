"""Unit tests for output_monitor.py (baseline + file-watch uploads)."""
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from output_monitor import META_FILE, OutputFileMonitor, load_baseline, write_baseline


class TestBaseline:
    def test_write_and_load_roundtrip(self, tmp_path):
        d = str(tmp_path)
        paths = {os.path.join(d, "a.txt"), os.path.join(d, "sub", "b.txt")}
        write_baseline(d, paths)
        assert load_baseline(d) == paths
        assert os.path.exists(os.path.join(d, META_FILE))

    def test_write_empty(self, tmp_path):
        write_baseline(str(tmp_path), set())
        assert load_baseline(str(tmp_path)) == set()

    def test_load_missing_returns_empty(self, tmp_path):
        assert load_baseline(str(tmp_path)) == set()

    def test_load_corrupt_returns_empty(self, tmp_path):
        with open(os.path.join(str(tmp_path), META_FILE), "w") as f:
            f.write("{bad")
        assert load_baseline(str(tmp_path)) == set()


class TestMonitor:
    def _monitor(self, tmp_path, **kwargs):
        store = MagicMock()
        store.upload_file.return_value = True
        monitor = OutputFileMonitor("job1", str(tmp_path), store, **kwargs)
        return monitor, store

    def test_scan_uploads_new_files(self, tmp_path):
        monitor, store = self._monitor(tmp_path)
        (tmp_path / "out.txt").write_text("data")
        monitor._scan()
        store.upload_file.assert_called_once_with(
            "job1/out.txt", str(tmp_path / "out.txt")
        )

    def test_excluded_files_skipped(self, tmp_path):
        excluded = str(tmp_path / "seed.txt")
        (tmp_path / "seed.txt").write_text("seed")
        monitor, store = self._monitor(tmp_path, exclude={excluded})
        monitor._scan()
        store.upload_file.assert_not_called()

    def test_unchanged_files_not_reuploaded(self, tmp_path):
        monitor, store = self._monitor(tmp_path)
        (tmp_path / "out.txt").write_text("data")
        monitor._scan()
        monitor._scan()
        assert store.upload_file.call_count == 1

    def test_changed_files_reuploaded(self, tmp_path):
        monitor, store = self._monitor(tmp_path)
        p = tmp_path / "out.txt"
        p.write_text("v1")
        monitor._scan()
        # force a different mtime/size signature
        os.utime(str(p), (0, 0))
        p.write_bytes(b"v1 plus more bytes here")
        monitor._scan()
        assert store.upload_file.call_count == 2

    def test_failed_upload_retried(self, tmp_path):
        monitor, store = self._monitor(tmp_path)
        store.upload_file.return_value = False
        (tmp_path / "out.txt").write_text("data")
        monitor._scan()
        monitor._scan()
        assert store.upload_file.call_count == 2

    def test_missing_dir_scan_is_noop(self, tmp_path):
        monitor, store = self._monitor(tmp_path)
        monitor.output_dir = str(tmp_path / "nope")
        monitor._scan()  # no raise
        store.upload_file.assert_not_called()

    def test_pending_uploads(self, tmp_path):
        monitor, store = self._monitor(tmp_path)
        assert monitor.pending_uploads() == []
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        pending = monitor.pending_uploads()
        assert len(pending) == 2
        monitor._scan()
        assert monitor.pending_uploads() == []

    def test_pending_skips_excluded(self, tmp_path):
        excluded = str(tmp_path / "seed.txt")
        (tmp_path / "seed.txt").write_text("seed")
        (tmp_path / "new.txt").write_text("new")
        monitor, store = self._monitor(tmp_path, exclude={excluded})
        assert monitor.pending_uploads() == [str(tmp_path / "new.txt")]

    def test_flush_scans_multiple_times(self, tmp_path):
        monitor, store = self._monitor(tmp_path)
        (tmp_path / "a.txt").write_text("a")
        with patch.object(monitor, "_scan") as mock_scan, patch(
            "output_monitor.time.sleep"
        ):
            monitor.flush(retries=3, retry_delay=0.1)
        assert mock_scan.call_count == 3

    def test_stop_sets_event_and_flushes(self, tmp_path):
        monitor, store = self._monitor(tmp_path)
        with (
            patch.object(monitor, "join"),
            patch.object(monitor, "flush") as mock_flush,
        ):
            monitor.stop()
        assert monitor._stop_event.is_set()
        mock_flush.assert_called_once()
