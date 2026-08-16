"""In-memory registry of running training jobs on this worker.

Coordinates the two owners of a job's lifecycle:
  * the executor  — creates the container, streams logs, finalizes the job
  * the checkpoint manager — pauses / resumes / periodically snapshots jobs

Each job maps to a JobEntry whose `state` is one of:
  running      — container is up and training
  checkpointing — a snapshot is being created (container may be stopped)
  paused       — snapshot stored, container frozen/stopped
  restoring    — being restored from a snapshot

Multi-GPU jobs that span several containers on the same node are tracked as
one JobEntry per container; the checkpoint manager coordinates them by
freezing every container before dumping any (NCCL state does not restore
cleanly on its own). Multi-node coordination is not implemented here.
"""
import threading
import time


class JobEntry:
    def __init__(self, job_id: str, container: str, image: str, checkpoint_dir: str):
        self.job_id = job_id
        self.container = container
        self.image = image
        self.checkpoint_dir = checkpoint_dir
        self.created_at = time.time()
        self.state = "running"
        self.latest_checkpoint = None
        self.last_checkpoint_at = 0.0
        self.checkpoint_interval = None
        self.lock = threading.RLock()

        self.output_dir = None
        self.store = None
        self.monitor = None

    def to_dict(self) -> dict:
        with self.lock:
            return {
                "job_id": self.job_id,
                "container": self.container,
                "image": self.image,
                "state": self.state,
                "latest_checkpoint": self.latest_checkpoint,
                "last_checkpoint_at": self.last_checkpoint_at,
                "created_at": self.created_at,
            }


class JobRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, JobEntry] = {}

    def add(self, entry: JobEntry):
        with self._lock:
            self._jobs[entry.job_id] = entry

    def get(self, job_id: str) -> JobEntry | None:
        with self._lock:
            return self._jobs.get(job_id)

    def remove(self, job_id: str):
        with self._lock:
            self._jobs.pop(job_id, None)

    def running_jobs(self) -> list[JobEntry]:
        with self._lock:
            entries = list(self._jobs.values())
        return [e for e in entries if e.state in ("running", "checkpointing")]

    def paused_jobs(self) -> list[JobEntry]:
        with self._lock:
            entries = list(self._jobs.values())
        return [e for e in entries if e.state == "paused"]

    def all(self) -> list[JobEntry]:
        with self._lock:
            return list(self._jobs.values())


registry = JobRegistry()
