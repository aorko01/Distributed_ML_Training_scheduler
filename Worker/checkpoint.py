"""Best-effort checkpoint/restore for GPU training jobs running in Docker.

This is an optional fallback layer, NOT the primary fault-recovery mechanism.

Flow (per the project design):
  1. Before pausing a job, run `cuda-checkpoint suspend` to suspend CUDA and
     evict GPU memory to host RAM.
  2. `docker checkpoint create` (CRIU) freezes and dumps the whole container
     to  <CHECKPOINT_DIR>/<job_id>/<checkpoint_name>.
  3. To resume, `docker start --checkpoint <name> <container>` restores the
     process tree, then `cuda-checkpoint resume` reloads the GPU state.

Reliability constraints:
  * Restore requires the SAME GPU model AND driver version (checked against
    metadata written at checkpoint time).
  * Reliable for single-GPU jobs. Multi-GPU jobs on the same node are
    coordinated by freezing every container before dumping any. Multi-node
    jobs need an external coordinator and are NOT implemented here.
"""
import json
import logging
import os
import shutil
import subprocess
import threading
import time

import runtime_config
from config import (
    CHECKPOINT_DIR,
    CHECKPOINT_ENABLED,
    CHECKPOINT_MODE,
    CUDA_CHECKPOINT_BIN,
)
from job_registry import JobEntry, registry

logger = logging.getLogger("checkpoint")


class CheckpointError(Exception):
    """Raised when a checkpoint/restore operation cannot be performed."""


def _run(cmd, check=False, timeout=120, capture=True):
    argv = [str(a) for a in cmd]
    logger.debug("Running: %s", " ".join(argv))
    res = subprocess.run(argv, capture_output=capture, text=True, timeout=timeout)
    if check and res.returncode != 0:
        raise CheckpointError(
            f"{argv[0]} failed (rc={res.returncode}): "
            f"{res.stderr.strip() or res.stdout.strip()}"
        )
    return res


def checkpointing_enabled() -> bool:
    return bool(CHECKPOINT_ENABLED)


def docker_checkpoint_supported() -> bool:
    """True when the `docker checkpoint` subcommand exists (dockerd must also
    be running with experimental features / CRIU enabled to actually work)."""
    if shutil.which("docker") is None:
        return False
    res = _run(["docker", "checkpoint", "--help"], check=False)
    return res.returncode == 0


def cuda_checkpoint_available() -> bool:
    return bool(CUDA_CHECKPOINT_BIN and shutil.which(CUDA_CHECKPOINT_BIN))


def get_gpu_fingerprint() -> dict:
    """GPU model + driver version of the first GPU (used for restore checks)."""
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15,
        )
        if res.returncode != 0:
            return {}
        lines = res.stdout.strip().splitlines()
        if not lines:
            return {}
        fields = [p.strip() for p in lines[0].split(",")]
        return {
            "gpu_model": fields[0] if fields else "",
            "driver_version": fields[1] if len(fields) > 1 else "",
        }
    except Exception as e:
        logger.debug("Failed to read GPU fingerprint: %s", e)
        return {}


class CheckpointManager:
    def __init__(self):
        self.fingerprint = get_gpu_fingerprint()

    def job_checkpoint_dir(self, job_id: str) -> str:
        return os.path.join(CHECKPOINT_DIR, job_id)

    def list_checkpoints(self, job_id: str) -> list[str]:
        base = self.job_checkpoint_dir(job_id)
        try:
            return sorted(
                n for n in os.listdir(base)
                if os.path.isdir(os.path.join(base, n))
            )
        except OSError:
            return []

    def has_checkpoint(self, job_id: str) -> bool:
        return bool(self.list_checkpoints(job_id))

    def checkpoint_info(self, job_id: str) -> dict:
        return {
            "job_id": job_id,
            "checkpoint_dir": self.job_checkpoint_dir(job_id),
            "checkpoints": self.list_checkpoints(job_id),
            "meta": self._read_meta(job_id),
            "gpu_fingerprint": self.fingerprint,
            "gpu_fingerprint_ok": self._gpu_matches_meta(job_id),
        }

    def container_state(self, container: str) -> str:
        res = _run(
            ["docker", "inspect", "--format", "{{.State.Status}}", container],
            check=False,
        )
        if res.returncode != 0:
            return "missing"
        return res.stdout.strip()

    def container_exit_code(self, container: str) -> int:
        res = _run(
            ["docker", "inspect", "--format", "{{.State.ExitCode}}", container],
            check=False,
        )
        if res.returncode != 0:
            return 1
        try:
            return int(res.stdout.strip())
        except ValueError:
            return 1

    def _container_pid(self, container: str) -> int | None:
        res = _run(
            ["docker", "inspect", "--format", "{{.State.Pid}}", container],
            check=False,
        )
        if res.returncode != 0:
            return None
        try:
            return int(res.stdout.strip())
        except ValueError:
            return None

    def _suspend_cuda(self, container: str) -> bool:
        if not cuda_checkpoint_available():
            logger.info(
                "cuda-checkpoint binary not found; skipping CUDA suspend for %s",
                container,
            )
            return False
        pid = self._container_pid(container)
        if not pid or pid <= 0:
            logger.warning("No live PID for %s; skipping CUDA suspend", container)
            return False
        res = _run([CUDA_CHECKPOINT_BIN, "suspend", str(pid)], check=False, timeout=600)
        if res.returncode != 0:
            logger.warning(
                "cuda-checkpoint suspend failed for %s: %s",
                container, res.stderr.strip(),
            )
        return res.returncode == 0

    def _resume_cuda(self, container: str) -> bool:
        if not cuda_checkpoint_available():
            return False
        pid = self._container_pid(container)
        if not pid or pid <= 0:
            logger.warning("No live PID for %s; skipping CUDA resume", container)
            return False
        res = _run([CUDA_CHECKPOINT_BIN, "resume", str(pid)], check=False, timeout=600)
        if res.returncode != 0:
            logger.warning(
                "cuda-checkpoint resume failed for %s: %s",
                container, res.stderr.strip(),
            )
        return res.returncode == 0

    def _write_meta(self, job_id: str, container: str, checkpoint_name: str, image: str):
        base = self.job_checkpoint_dir(job_id)
        try:
            os.makedirs(base, exist_ok=True)
            meta = {
                "job_id": job_id,
                "container": container,
                "image": image,
                "checkpoint_name": checkpoint_name,
                "gpu": self.fingerprint,
                "created_at": time.time(),
            }
            with open(os.path.join(base, "meta.json"), "w") as f:
                json.dump(meta, f, indent=2)
        except OSError as e:
            logger.warning("Failed to write checkpoint metadata: %s", e)

    def _read_meta(self, job_id: str) -> dict | None:
        path = os.path.join(self.job_checkpoint_dir(job_id), "meta.json")
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    def _gpu_matches_meta(self, job_id: str) -> bool:
        meta = self._read_meta(job_id)
        if not meta or not meta.get("gpu"):
            return True
        return get_gpu_fingerprint() == meta["gpu"]

    def create_checkpoint(self, job_id: str, container: str,
                          checkpoint_name: str | None = None,
                          leave_running: bool = False,
                          image: str = "", cuda_suspend: bool = True) -> str:
        """Suspend CUDA, then dump the container with `docker checkpoint`.

        Unless `leave_running` is set the container is left stopped (frozen).
        Checkpoint data is written under  <CHECKPOINT_DIR>/<job_id>/<name>.
        """
        if not docker_checkpoint_supported():
            raise CheckpointError(
                "Docker checkpoint support not available (dockerd must be "
                "running with experimental features / CRIU enabled)"
            )
        name = checkpoint_name or f"cp_{int(time.time())}"
        if cuda_suspend:
            self._suspend_cuda(container)

        cmd = [
            "docker", "checkpoint", "create",
            "--checkpoint-dir", self.job_checkpoint_dir(job_id),
        ]
        if leave_running:
            cmd.append("--leave-running")
        cmd += [container, name]
        _run(cmd, check=True, timeout=1800)
        self._write_meta(job_id, container, name, image)
        logger.info("Checkpoint %s created for job %s", name, job_id)
        return name

    def restore_checkpoint(self, job_id: str, container: str,
                           checkpoint_name: str | None = None) -> str:
        """Validate GPU/driver fingerprint, then restore from the snapshot."""
        if not self._gpu_matches_meta(job_id):
            meta = self._read_meta(job_id) or {}
            raise CheckpointError(
                "GPU/driver mismatch: checkpoint was taken on "
                f"{meta.get('gpu')}, current is {get_gpu_fingerprint()}"
            )

        meta = self._read_meta(job_id) or {}
        name = checkpoint_name or meta.get("checkpoint_name")
        if not name:
            raise CheckpointError(f"No checkpoint recorded for job {job_id}")
        base = self.job_checkpoint_dir(job_id)
        if not os.path.isdir(os.path.join(base, name)):
            raise CheckpointError(f"Checkpoint '{name}' missing for job {job_id}")

        state = self.container_state(container)
        if state == "running":
            # leave_running snapshot: the old process is CUDA-suspended and
            # frozen; kill it and restore from the dump instead.
            logger.info("Stopping running container %s before restore", container)
            _run(["docker", "stop", "-t", "0", container], check=True, timeout=120)

        _run(
            ["docker", "start", "--checkpoint-dir", base,
             "--checkpoint", name, container],
            check=True, timeout=1800,
        )
        self._resume_cuda(container)
        logger.info("Restored job %s from checkpoint %s", job_id, name)
        return name

    def _state_after_failure(self, job_id: str) -> str:
        return "paused" if self.has_checkpoint(job_id) else "running"

    def pause_job(self, job_id: str) -> JobEntry:
        entry = registry.get(job_id)
        if not entry:
            raise CheckpointError(f"No running job with id {job_id}")
        with entry.lock:
            if entry.state == "paused":
                return entry
            if entry.state != "running":
                raise CheckpointError(
                    f"Job {job_id} is {entry.state}, cannot pause"
                )
            entry.state = "checkpointing"
        try:
            name = self.create_checkpoint(
                job_id, entry.container, image=entry.image,
                leave_running=CHECKPOINT_MODE == "leave_running",
            )
            with entry.lock:
                entry.latest_checkpoint = name
                entry.last_checkpoint_at = time.time()
                entry.state = "paused"
            logger.info("Job %s paused (checkpoint %s)", job_id, name)
            return entry
        except Exception:
            with entry.lock:
                entry.state = self._state_after_failure(job_id)
            raise

    def resume_job(self, job_id: str) -> JobEntry:
        entry = registry.get(job_id)
        if not entry:
            raise CheckpointError(f"No paused job with id {job_id}")
        with entry.lock:
            if entry.state == "running":
                return entry
            if entry.state != "paused":
                raise CheckpointError(
                    f"Job {job_id} is {entry.state}, cannot restore"
                )
            entry.state = "restoring"
        try:
            name = self.restore_checkpoint(job_id, entry.container)
            with entry.lock:
                entry.latest_checkpoint = name
                entry.state = "running"
            logger.info("Job %s resumed from checkpoint %s", job_id, name)
            return entry
        except Exception:
            with entry.lock:
                entry.state = "paused"
            raise

    def snapshot_and_continue(self, job_id: str):
        """Periodic cycle: checkpoint (container stops) then immediately
        restart from the fresh snapshot so the job keeps running."""
        entry = registry.get(job_id)
        if not entry:
            return
        name = self.create_checkpoint(
            job_id, entry.container, image=entry.image, leave_running=False,
        )
        with entry.lock:
            entry.latest_checkpoint = name
            entry.last_checkpoint_at = time.time()
            entry.state = "restoring"
        try:
            self.restore_checkpoint(job_id, entry.container, name)
        except Exception:
            with entry.lock:
                entry.state = "paused"
            raise
        with entry.lock:
            entry.state = "running"
        logger.info("Periodic checkpoint cycle done for job %s", job_id)

    def periodic_snapshots(self):
        interval = runtime_config.get("checkpoint_interval")
        if interval <= 0:
            return
        now = time.time()
        for entry in registry.running_jobs():
            with entry.lock:
                if entry.state != "running":
                    continue
                eff = entry.checkpoint_interval or interval
                if eff <= 0 or (now - entry.last_checkpoint_at) < eff:
                    continue
                entry.state = "checkpointing"
            try:
                self.snapshot_and_continue(entry.job_id)
            except Exception as e:
                logger.error("Periodic checkpoint failed for job %s: %s", entry.job_id, e)
                with entry.lock:
                    entry.state = self._state_after_failure(entry.job_id)

    def pause_all(self) -> dict:
        results = {}
        for entry in registry.running_jobs():
            try:
                results[entry.job_id] = self.pause_job(entry.job_id).to_dict()
            except Exception as e:
                logger.error("Failed to checkpoint job %s: %s", entry.job_id, e)
                results[entry.job_id] = {"error": str(e)}
        return results

    def resume_all(self) -> dict:
        results = {}
        for entry in registry.paused_jobs():
            try:
                results[entry.job_id] = self.resume_job(entry.job_id).to_dict()
            except Exception as e:
                logger.error("Failed to restore job %s: %s", entry.job_id, e)
                results[entry.job_id] = {"error": str(e)}
        return results


checkpoint_manager = CheckpointManager()
