import os
import threading
import time
import logging

from object_store import ObjectStore

logger = logging.getLogger("output_monitor")


class OutputFileMonitor(threading.Thread):
    """Watches a job's output directory and uploads new/changed files to the object store."""

    def __init__(
        self,
        job_id: str,
        output_dir: str,
        store: ObjectStore,
        poll_interval: float = 2.0,
        exclude: set[str] | None = None,
    ):
        super().__init__(daemon=True)
        self.job_id = job_id
        self.output_dir = output_dir
        self.store = store
        self.poll_interval = poll_interval
        self._exclude = exclude or set()
        self._stop_event = threading.Event()
        self._uploaded: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def run(self):
        while not self._stop_event.is_set():
            self._scan()
            time.sleep(self.poll_interval)

    def stop(self, retries: int = 3, retry_delay: float = 0.5):
        self._stop_event.set()
        self.join()
        self.flush(retries=retries, retry_delay=retry_delay)

    def flush(self, retries: int = 3, retry_delay: float = 0.5):
        """Final scan, retrying until all files are uploaded (or attempts exhausted)."""
        for attempt in range(retries):
            self._scan()
            if attempt < retries - 1:
                time.sleep(retry_delay)

    def _scan(self):
        if not os.path.isdir(self.output_dir):
            return
        for root, _dirs, files in os.walk(self.output_dir):
            for name in files:
                self._maybe_upload(os.path.join(root, name))

    def _maybe_upload(self, file_path: str):
        if file_path in self._exclude:
            return
        try:
            stat = os.stat(file_path)
        except OSError:
            return

        object_key = f"{self.job_id}/{os.path.relpath(file_path, self.output_dir)}"
        signature = (stat.st_size, stat.st_mtime)

        with self._lock:
            if self._uploaded.get(object_key) == signature:
                return

        if self.store.upload_file(object_key, file_path):
            with self._lock:
                self._uploaded[object_key] = signature
