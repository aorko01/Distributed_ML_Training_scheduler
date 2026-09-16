import os
import json
import shlex
import shutil
import logging
import subprocess
import tempfile
import time
import uuid
import threading
import docker

from config import (
    OUTPUT_DIR, VRAM_ESTIMATION_SCRIPT, DOCKER_HUB_USERNAME,
    CONTAINER_OUTPUT_MOUNT, CONTAINER_AS_ROOT,
)
from api import SchedulerAPI
from object_store import ObjectStore
from output_monitor import META_FILE, OutputFileMonitor, write_baseline, load_baseline
from telemetry import record_job, record_event
from job_state import (
    load_running_job, load_running_jobs, save_running_job, clear_running_job,
)
from hardware import get_gpu_info
import runtime_config

logger = logging.getLogger("executor")


class _JobLogState:
    def __init__(self):
        self.build_log_base: str | None = None
        self.last_log_upload: float | None = None
        self.log_push_buffer: list[str] = []
        self.last_log_push: float | None = None
        self.job_log_buffer: list[str] = []


def _docker_host_path(path: str) -> str:
    """Normalize a host path for use in a `docker run -v` / `docker cp` spec.

    Docker for Windows accepts Windows-style paths but prefers forward slashes;
    normalizing avoids backslash escaping issues in the CLI. On POSIX the path
    is returned unchanged.
    """
    if os.name == "nt":
        return path.replace("\\", "/")
    return path


class JobExecutor:
    def __init__(self, api: SchedulerAPI):
        self.api = api
        # Always define the attribute so later accesses fail with a clear
        # Docker error (caught → False/None) instead of AttributeError.
        self.docker_client = None
        try:
            self.docker_client = docker.from_env()
        except Exception as e:
            logger.error("Failed to connect to Docker daemon: %s", e)

        # docker-py's API client owns a requests Session, which is not safe to
        # mutate from several job threads at once.  Keep SDK operations short
        # and serialized; the long-running containers themselves use the CLI
        # and still execute concurrently.
        self._docker_client_lock = threading.Lock()

        self._active_jobs_lock = threading.Lock()
        self._active_jobs: dict[str, dict] = {}
        self._pending: set[str] = set()
        self._resuming: set[str] = set()
        self._resume_scan_active = False

        self._job_logs_lock = threading.Lock()
        self._job_logs: dict[str, _JobLogState] = {}

    @property
    def active_jobs_count(self) -> int:
        with self._active_jobs_lock:
            # Unstarted job/resume reservations have priority over pulling new
            # work. Reporting the worker as full closes the window where the
            # polling thread could claim a slot before the reserved thread has
            # registered its job and VRAM requirement.
            if self._resume_scan_active or self._pending or self._resuming:
                return int(runtime_config.get("max_concurrent_jobs") or 2)
            return len(self._active_jobs) + len(self._resuming)

    def is_job_active(self, job_id: str) -> bool:
        with self._active_jobs_lock:
            return job_id in self._active_jobs

    def get_active_job_ids(self) -> set[str]:
        with self._active_jobs_lock:
            return set(self._active_jobs.keys())

    def begin_resume(self, job_id: str) -> bool:
        with self._active_jobs_lock:
            max_jobs = int(runtime_config.get("max_concurrent_jobs") or 2)
            occupied = len(self._active_jobs) + len(self._resuming)
            if (
                occupied >= max_jobs
                or job_id in self._active_jobs
                or job_id in self._resuming
            ):
                return False
            self._resuming.add(job_id)
            return True

    def end_resume(self, job_id: str):
        with self._active_jobs_lock:
            self._resuming.discard(job_id)

    def try_begin_resume_scan(self) -> bool:
        """Reserve the single persisted-job scan allowed at a time."""
        with self._active_jobs_lock:
            if self._resume_scan_active:
                return False
            self._resume_scan_active = True
            return True

    def end_resume_scan(self):
        with self._active_jobs_lock:
            self._resume_scan_active = False

    def get_effective_free_vram(self, free_vram: float, total_vram: float = 0.0) -> float:
        """Compute free VRAM accounting for active jobs whose allocations may
        not yet be reflected by GPUtil (e.g. image pulling or startup phase)."""
        with self._active_jobs_lock:
            active_required = sum(
                float(job.get("vram_required") or 0.0)
                for job in self._active_jobs.values()
            )
        if active_required <= 0.0:
            return free_vram

        if total_vram > 0.0:
            current_used = max(0.0, total_vram - free_vram)
            unobserved = max(0.0, active_required - current_used)
            return round(max(0.0, free_vram - unobserved), 2)
        else:
            return round(max(0.0, free_vram - active_required), 2)

    def has_unresumed_job(self) -> bool:
        """True if there is any persisted job not currently actively executing."""
        persisted = []
        try:
            persisted = load_running_jobs()
        except Exception:
            persisted = []
        if not persisted:
            single = load_running_job()
            if single:
                persisted = [single]
        with self._active_jobs_lock:
            claimed = set(self._active_jobs) | set(self._resuming)
        for item in persisted:
            jid = item.get("job_id") if isinstance(item, dict) else None
            if jid and jid not in claimed:
                return True
        return False

    @staticmethod
    def _record_job(job_id: str, image_name: str, flag: str, status: str,
                    started_at: float, vram_estimate_gb: float = 0.0):
        job_type = "estimation" if flag == "vram_estimation" else "training"
        record_job({
            "id": job_id,
            "image": image_name,
            "type": job_type,
            "status": status,
            "vramEstimateGb": vram_estimate_gb,
            "startedAt": time.strftime("%H:%M:%S", time.localtime(started_at)),
            "durationSec": int(time.time() - started_at),
        })
        record_event(
            "success" if status == "completed" else "error",
            f"Job {job_id} ({job_type}) {status}",
        )

    def pull_docker_image(self, image_name: str) -> bool:
        logger.info("Pulling Docker image: %s", image_name)
        try:
            with self._docker_client_lock:
                self.docker_client.images.pull(image_name)
            logger.info("Successfully pulled image: %s", image_name)
            return True
        except Exception as e:
            logger.error("Failed to pull image %s: %s", image_name, e)
            return False

    def _image_workdir(self, image_name: str) -> str | None:
        """Return the image's configured WORKDIR, or None if it can't be read."""
        try:
            with self._docker_client_lock:
                image = self.docker_client.images.get(image_name)
            return (image.attrs.get("Config", {}) or {}).get("WorkingDir") or None
        except Exception as e:
            logger.debug("Failed to read WORKDIR for %s: %s", image_name, e)
            return None

    def _resolve_mount_target(self, image_name: str) -> str | None:
        """Container path to mount the job output dir at (the image's WORKDIR),
        or None when it can't be used (WORKDIR is root fs)."""
        workdir = self._image_workdir(image_name) or "/workspace"
        if workdir == "/":
            logger.warning(
                "Refusing to mount output dir over root fs; "
                "falling back to %s", CONTAINER_OUTPUT_MOUNT,
            )
            return None
        return workdir

    def _prepare_output_mount(self, job_output_dir: str,
                              image_name: str) -> tuple[str, set[str]]:
        """Make the host output dir behave as the container's working directory.

        Seeds the output dir with the image's WORKDIR contents and returns the
        container path to mount it at, so anything the container writes relative
        to its working directory lands in <OUTPUT_DIR>/<job_id> regardless of
        what the user's code writes. Falls back to the conventional
        CONTAINER_OUTPUT_MOUNT mount if the workdir can't be prepared.

        The second return value is the baseline set of absolute file paths that
        existed after seeding (i.e. the image's baked-in files); the output
        monitor uses it to skip those files so only files generated by the
        running code are uploaded to the object store.
        """
        workdir = self._resolve_mount_target(image_name)
        if workdir is None:
            return CONTAINER_OUTPUT_MOUNT, set()

        seed_name = f"seed-{uuid.uuid4().hex[:12]}"
        try:
            subprocess.run(
                ["docker", "create", "--name", seed_name, image_name],
                check=True, capture_output=True, text=True,
            )
            subprocess.run(
                ["docker", "cp", f"{seed_name}:{workdir}/.",
                 _docker_host_path(job_output_dir)],
                check=True, capture_output=True, text=True,
            )
            logger.info("Seeded %s with %s WORKDIR contents (%s)",
                        job_output_dir, image_name, workdir)
        except subprocess.CalledProcessError as e:
            logger.warning("Failed to seed output dir from %s: %s",
                           image_name, e.stderr.strip())
            return CONTAINER_OUTPUT_MOUNT, set()
        finally:
            subprocess.run(
                ["docker", "rm", "-f", seed_name],
                capture_output=True, text=True,
            )

        baseline = set()
        for root, _dirs, files in os.walk(job_output_dir):
            for name in files:
                baseline.add(os.path.join(root, name))
        write_baseline(job_output_dir, baseline)
        baseline.add(os.path.join(job_output_dir, META_FILE))
        return workdir, baseline

    @staticmethod
    def _container_user_args() -> list[str]:
        """Run containers as the worker's UID/GID so files written into the
        output mount are owned by the worker (readable for upload, deletable
        on cleanup). Falls back to root (no args) when CONTAINER_AS_ROOT=1.

        Passing a numeric --user only makes sense on POSIX hosts; under Docker
        Desktop on Windows the container's root maps back to the host user, so
        we skip the flag there (os.getuid/getgid don't exist on Windows).
        """
        if CONTAINER_AS_ROOT:
            return []
        if os.name != "posix" or not hasattr(os, "getuid"):
            return []
        return [
            "--user", f"{os.getuid()}:{os.getgid()}",
            "-e", "HOME=/tmp",
        ]

    @staticmethod
    def _parse_python_command(command: str) -> list[str] | None:
        """Extract purely the python execution arguments from a command string."""
        try:
            command_args = json.loads(command) if command.lstrip().startswith("[") else shlex.split(command)
        except (json.JSONDecodeError, ValueError):
            return None

        for index, value in enumerate(command_args):
            if os.path.basename(value).startswith("python"):
                command_args = command_args[index + 1:]
                while command_args and command_args[0].startswith("-"):
                    command_args.pop(0)
                return command_args or None
        return None

    def handle_vram_estimation(self, job_id: str, image_name: str, command: str):
        started_at = time.time()
        target_command = self._parse_python_command(command)
        if not target_command:
            logger.error("Job %s needs a Python command for VRAM estimation.", job_id)
            self._record_job(job_id, image_name, "vram_estimation", "failed", started_at)
            self.api.mark_job_failed(
                job_id, "user", "Job needs a Python command for VRAM estimation"
            )
            return

        with tempfile.TemporaryDirectory(prefix=f"vram_{job_id}_") as report_dir:
            report_path = os.path.join(report_dir, "report.json")
            cmd = [
                "docker", "run", "--rm", "--gpus", "all",
                "-v", f"{_docker_host_path(VRAM_ESTIMATION_SCRIPT)}:/vram_estimation.py:ro",
                "-v", f"{_docker_host_path(report_dir)}:/report",
                "--entrypoint", "python", image_name,
                "/vram_estimation.py", "--output", "/report/report.json", *target_command,
            ]
            
            logger.info("Running VRAM estimation for job %s.", job_id)
            record_event("info", f"Job {job_id} VRAM estimation started")
            try:
                result = subprocess.run(cmd, capture_output=True, text=True)
            except Exception as e:
                logger.error("Failed to run VRAM estimation for job %s: %s", job_id, e)
                self._record_job(job_id, image_name, "vram_estimation", "failed", started_at)
                self.api.mark_job_failed(
                    job_id, "system", f"Failed to run VRAM estimation container: {e}"
                )
                return
            
            if result.returncode != 0:
                reason = result.stderr.strip() or f"VRAM estimation exited with code {result.returncode}"
                logger.error("VRAM estimation failed for job %s: %s", job_id, reason)
                self._record_job(job_id, image_name, "vram_estimation", "failed", started_at)
                self.api.mark_job_failed(job_id, "user", reason)
                return
            
            try:
                with open(report_path, encoding="utf-8") as report_file:
                    report = json.load(report_file)
                if report.get("step_wall_time") is None:
                    raise ValueError("No optimizer steps were observed")
            except (OSError, ValueError, json.JSONDecodeError) as e:
                logger.error("Invalid VRAM estimation report for job %s: %s", job_id, e)
                self._record_job(job_id, image_name, "vram_estimation", "failed", started_at)
                self.api.mark_job_failed(
                    job_id, "user", f"Invalid VRAM estimation report: {e}"
                )
                return

        self.api.save_vram_estimation(job_id, report)
        self._record_job(
            job_id, image_name, "vram_estimation", "completed",
            started_at, round(report.get("peak_reserved_memory", 0.0), 2),
        )

    def _reset_log_state(self, job_id: str | None = None):
        """Clear per-run log buffers/throttle markers before a container run."""
        if job_id is None:
            return
        state = self._get_job_log_state(job_id)
        state.build_log_base = None
        state.last_log_upload = None
        state.log_push_buffer = []
        state.last_log_push = None
        state.job_log_buffer = []

    def _get_job_log_state(self, job_id: str | None) -> _JobLogState | None:
        """Retrieve or create the per-job log state. Returns None for legacy
        callers that pass no job_id (existing tests that manipulate the legacy
        attributes directly)."""
        if job_id is None:
            return None
        with self._job_logs_lock:
            if job_id not in self._job_logs:
                self._job_logs[job_id] = _JobLogState()
            return self._job_logs[job_id]

    def _drop_job_log_state(self, job_id: str):
        with self._job_logs_lock:
            self._job_logs.pop(job_id, None)

    def handle_training(self, job_id: str, image_name: str):
        started_at = time.time()
        logger.info("Training job received for job %s.", job_id)
        record_event("info", f"Job {job_id} training started")

        job_output_dir = os.path.join(OUTPUT_DIR, job_id)
        # Start from a clean slate: a leftover dir could hold stale files from a
        # previous attempt that never got cleaned up.
        self._remove_output_dir(job_output_dir)
        os.makedirs(job_output_dir, exist_ok=True)

        mount_target, baseline = self._prepare_output_mount(job_output_dir, image_name)

        store = ObjectStore()
        monitor = OutputFileMonitor(job_id, job_output_dir, store, exclude=baseline)
        monitor.start()

        self._reset_log_state(job_id)
        self._run_container(
            job_id, image_name, job_output_dir, mount_target, store,
            monitor, started_at,
        )

    def handle_retry(self, job_id: str, image_name: str,
                     resume_command: str | None, original_command: str | None):
        started_at = time.time()
        logger.info("Retry job received for job %s.", job_id)
        record_event("info", f"Job {job_id} retry started")

        job_output_dir = os.path.join(OUTPUT_DIR, job_id)
        store = ObjectStore()

        # First try to pick up where the last run left off: restore the saved
        # checkpoints from the object store and run the resume command.
        if resume_command:
            resume_result = self._resume_attempt(
                job_id, image_name, job_output_dir, store,
                resume_command, started_at,
            )
            if resume_result is None:
                return

            failure_type, failure_reason = resume_result
            if failure_type == "system":
                # The resume container could not even start (infra issue, e.g.
                # busy GPU / docker daemon) — not a checkpoint problem, so a
                # fresh run would just burn GPU time and fail the same way.
                # Requeue via the normal failure path instead.
                logger.error("Job %s: resume could not start (%s).",
                             job_id, failure_reason)
                self._record_job(job_id, image_name, "training", "failed", started_at)
                self.api.mark_job_failed(job_id, failure_type, failure_reason)
                clear_running_job(job_id)
                return

            logger.warning(
                "Job %s: resume with restored checkpoints failed (%s); starting fresh.",
                job_id, failure_type,
            )

        # Resume failed (e.g. corrupted checkpoints) or there is no resume
        # command — discard everything and run the original training command
        # from scratch. If that fails too, it is a genuine code error and the
        # job is marked FAILED normally.
        if not original_command:
            logger.error("Job %s needs an original command to start fresh.", job_id)
            self._record_job(job_id, image_name, "training", "failed", started_at)
            self.api.mark_job_failed(
                job_id, "system", "Retry job has no original command"
            )
            clear_running_job(job_id)
            return

        logger.info("Job %s: starting fresh training run.", job_id)
        record_event("info", f"Job {job_id} starting fresh training run")
        self.handle_training(job_id, image_name)

    def _resume_attempt(self, job_id: str, image_name: str,
                        job_output_dir: str, store: ObjectStore,
                        resume_command: str, started_at: float):
        """Try to continue a job from its last checkpoints.

        If the output dir from a previous run is still on disk (this worker
        crashed and restarted), reuse those local checkpoints directly instead
        of re-uploading them to the object store and downloading them back. Only
        when there is no usable local state (a retry pulled from the scheduler,
        or a crash before the workspace baseline was recorded) does it seed the
        workspace and restore the last saved outputs from the object store.

        Returns None if the resumed run completed successfully, or a
        (failure_type, failure_reason) tuple if it failed, in which case the
        caller decides whether to discard the restored outputs and start fresh.
        """
        local_baseline = os.path.isfile(os.path.join(job_output_dir, META_FILE))
        if local_baseline:
            mount_target = self._resolve_mount_target(image_name) or CONTAINER_OUTPUT_MOUNT
            baseline = load_baseline(job_output_dir)
            baseline.add(os.path.join(job_output_dir, META_FILE))
            logger.info(
                "Job %s: resuming from local checkpoints in %s",
                job_id, job_output_dir,
            )
        else:
            self._remove_output_dir(job_output_dir)
            os.makedirs(job_output_dir, exist_ok=True)

            mount_target, baseline = self._prepare_output_mount(job_output_dir, image_name)

            # Restore the checkpoints into the workspace after the baseline is
            # recorded, so only the image's baked-in files are excluded from
            # uploads and the resumed run's updated checkpoints still get pushed.
            self._restore_job_output(job_id, job_output_dir, store)

        monitor = OutputFileMonitor(job_id, job_output_dir, store, exclude=baseline)
        monitor.start()

        self._reset_log_state(job_id)

        success, failure_type, failure_reason = self._run_container(
            job_id, image_name, job_output_dir, mount_target, store,
            monitor, started_at,
            command_args=["sh", "-c", resume_command],
            finalize=False,
        )

        if success:
            logger.info("Job %s: resumed successfully.", job_id)
            self._finalize_job(job_id, image_name, job_output_dir, monitor,
                               started_at, True)
            return None

        logger.error("Job %s: resume failed (%s: %s).",
                     job_id, failure_type, failure_reason)
        self._remove_output_dir(job_output_dir)
        return failure_type, failure_reason

    def _restore_job_output(self, job_id: str, job_output_dir: str, store: ObjectStore):
        """Download the job's previously uploaded outputs from the object store
        into the container's workspace."""
        objects = store.list_objects(prefix=f"{job_id}/")
        restored = 0
        for obj in objects:
            key = obj.get("key", "")
            rel_path = key[len(job_id) + 1:] if key.startswith(f"{job_id}/") else key
            if not rel_path:
                continue
            # build.log is a build artifact, not training state; skip it.
            if os.path.basename(rel_path) == "build.log":
                continue

            dest = os.path.join(job_output_dir, rel_path)
            if store.download_to(key, dest, size=obj.get("size")):
                restored += 1
            else:
                logger.warning(
                    "Job %s: failed to restore %s from object store.", job_id, key
                )

        if objects:
            logger.info("Job %s: restored %d/%d file(s) from object store.",
                        job_id, restored, len(objects))
        else:
            logger.info("Job %s: no previously saved outputs to restore.", job_id)

    def _run_container(self, job_id: str, image_name: str,
                              job_output_dir: str, mount_target: str,
                              store: ObjectStore, monitor: OutputFileMonitor,
                              started_at: float, command_args: list[str] | None = None,
                              finalize: bool = True):
        cmd = [
            "docker", "run", "--rm", "--gpus", "all",
            *self._container_user_args(),
            "-v", f"{_docker_host_path(job_output_dir)}:{mount_target}",
            image_name,
        ]
        if command_args:
            cmd.extend(command_args)
        logger.info("Running container: %s", " ".join(cmd))

        job_log = self._get_job_log_state(job_id)
        success = False
        failure_type = "system"
        failure_reason = "Training container failed to start"
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )

            for line in iter(proc.stdout.readline, ""):
                line = line.rstrip("\r\n")
                if job_log is not None:
                    job_log.job_log_buffer.append(line)
                    job_log.log_push_buffer.append(line)
                self._flush_log_push(job_id)
                logger.info("[job %s] %s", job_id, line)

                last_upload = job_log.last_log_upload if job_log is not None else None
                if time.monotonic() - (last_upload or 0) >= runtime_config.get("log_upload_interval"):
                    if job_log is not None:
                        self._append_build_log(job_id, store, job_log.job_log_buffer)

            proc.wait()
            success = proc.returncode == 0
            if not success:
                failure_type = "user"
                failure_reason = f"Training command exited with code {proc.returncode}"
        except Exception as e:
            failure_type = "system"
            failure_reason = f"Execution error: {e}"
            logger.error("Execution error for job %s: %s", job_id, e)

        self._flush_log_push(job_id, force=True)
        if job_log is not None:
            self._append_build_log(job_id, store, job_log.job_log_buffer, force=True)
        monitor.stop()

        if not finalize:
            return success, failure_type, failure_reason

        self._finalize_job(job_id, image_name, job_output_dir, monitor,
                           started_at, success, failure_type, failure_reason)

    def _remove_output_dir(self, job_output_dir: str) -> bool:
        """Delete a job output dir, falling back to a root docker helper when
        it contains files/dirs owned by the container's root user."""
        shutil.rmtree(job_output_dir, ignore_errors=True)
        if not os.path.exists(job_output_dir):
            return True
        try:
            # Delete the *contents* of the bind mount, not the mountpoint
            # itself: `rm -rf /cleanup` tries to unlink the mounted dir
            # (often EBUSY) and reports failure even after deleting everything.
            subprocess.run(
                [
                    "docker", "run", "--rm",
                    "-v", f"{_docker_host_path(job_output_dir)}:/cleanup",
                    "alpine", "sh", "-c", "rm -rf /cleanup/* /cleanup/.[!.]* /cleanup/..?* 2>/dev/null; true",
                ],
                check=True, capture_output=True, text=True, timeout=180,
            )
            shutil.rmtree(job_output_dir, ignore_errors=True)
            return not os.path.exists(job_output_dir)
        except Exception as e:
            logger.warning("Docker-assisted cleanup failed for %s: %s",
                           job_output_dir, e)
            return False

    def _finalize_job(self, job_id: str, image_name: str, job_output_dir: str,
                      monitor: OutputFileMonitor, started_at: float,
                      success: bool, failure_type: str = "system",
                      failure_reason: str = ""):
        monitor.flush()
        pending = monitor.pending_uploads()
        if pending:
            logger.warning(
                "Job %s: %d file(s) failed to upload to object store; "
                "keeping output dir at %s",
                job_id, len(pending), job_output_dir,
            )
        elif self._remove_output_dir(job_output_dir):
            logger.info("Deleted output directory for job %s.", job_id)
        else:
            logger.warning("Could not fully delete output dir for job %s: %s",
                           job_id, job_output_dir)

        if success:
            logger.info("Job %s completed successfully.", job_id)
            self.api.mark_job_completed(job_id)
            self._record_job(job_id, image_name, "training", "completed", started_at)
        else:
            logger.error("Job %s failed.", job_id)
            self._record_job(job_id, image_name, "training", "failed", started_at)
            self.api.mark_job_failed(job_id, failure_type, failure_reason)

        self._drop_job_log_state(job_id)
        clear_running_job(job_id)

    @staticmethod
    def _throttled(last: float | None, interval: float, force: bool) -> bool:
        """True when a periodic action should be skipped due to throttling."""
        return (
            not force
            and last is not None
            and (time.monotonic() - last) < interval
        )

    def _flush_log_push(self, job_id: str, force: bool = False):
        state = self._get_job_log_state(job_id)
        if state is None or not state.log_push_buffer:
            return
        now = time.monotonic()
        if self._throttled(
            state.last_log_push, runtime_config.get("log_push_interval"), force
        ):
            return
        lines = state.log_push_buffer
        state.log_push_buffer = []
        state.last_log_push = now
        if not self.api.send_logs(job_id, lines):
            # Preserve ordering when a transient scheduler/Redis failure makes
            # a push fail.  A later periodic or final flush will retry it.
            state.log_push_buffer = lines + state.log_push_buffer

    def _append_build_log(
        self, job_id: str, store: ObjectStore, log_buffer: list[str], force: bool = False
    ):
        if not log_buffer:
            return

        state = self._get_job_log_state(job_id)
        if state is None:
            return

        build_log_base = state.build_log_base
        last_log_upload = state.last_log_upload

        now = time.monotonic()
        if self._throttled(
            last_log_upload, runtime_config.get("log_upload_interval"), force
        ):
            return

        if build_log_base is None:
            existing = store.download(f"{job_id}/build.log")
            build_log_base = (
                existing.decode("utf-8", errors="replace") if existing else ""
            )

        content = build_log_base
        if content and not content.endswith("\n"):
            content += "\n"
        content += "\n".join(log_buffer) + "\n"

        if store.upload_bytes(
            f"{job_id}/build.log", content.encode("utf-8"), "text/plain"
        ):
            state.build_log_base = build_log_base
            state.last_log_upload = now

    def process_job(self, job: dict):
        job_id = job.get("job_id") or job.get("id")
        flag = job.get("flag", "training")
        if not job_id:
            logger.error("Received job without job_id/id: %s", job)
            return
        if "/" in str(job_id) or "\\" in str(job_id) or ".." in str(job_id):
            logger.error("Rejecting job with unsafe job_id %r", job_id)
            try:
                self.api.mark_job_failed(job_id, "user", f"Invalid job_id: {job_id!r}")
            except Exception:
                pass
            # The polling loop reserves capacity before starting this method.
            # An early validation return must release that reservation.
            self._unregister_job(job_id)
            return
        image_name = job.get("image_tag") or f"{DOCKER_HUB_USERNAME}/{job_id}:latest"
        vram_required = job.get("vram_required")

        self._register_job(job_id, vram_required)
        self._finalize_job_log_state(job_id)

        try:
            if not self.pull_docker_image(image_name):
                logger.error("Aborting job %s: image pull failed.", job_id)
                self.api.mark_job_failed(
                    job_id, "system", f"Failed to pull Docker image {image_name}"
                )
                return

            if flag == "vram_estimation":
                self.handle_vram_estimation(job_id, image_name, job.get("command", ""))
            elif flag == "training":
                save_running_job(job_id)
                self.handle_training(job_id, image_name)
            elif flag == "retry":
                save_running_job(job_id)
                self.handle_retry(job_id, image_name, job.get("resume_command"),
                                  job.get("command"))
            else:
                logger.warning("Unknown job flag '%s' for job %s.", flag, job_id)
                try:
                    self.api.mark_job_failed(job_id, "system", f"Unknown job flag: {flag}")
                except Exception:
                    pass
        except Exception as e:
            logger.exception("Unhandled error while processing job %s", job_id)
            self.api.mark_job_failed(job_id, "system", f"Worker execution error: {e}")
            if flag in ("training", "retry"):
                clear_running_job(job_id)
        finally:
            self._drop_job_log_state(job_id)
            self._unregister_job(job_id)

    def _resume_reserved_job(self, job_id: str) -> bool:
        """Resume one persisted job whose capacity slot is already reserved."""
        activated = False
        try:
            gpu_type, _, _, _, _ = get_gpu_info()
            job = self.api.resume_job(job_id, gpu_type)
            if job is None:
                logger.info("Job %s is no longer in progress on this worker; "
                            "dropping local resume state.", job_id)
                clear_running_job(job_id)
                return False

            self._register_job(job_id, job.get("vram_required"))
            activated = True
            self._finalize_job_log_state(job_id)
            image_name = job.get("image_tag") or f"{DOCKER_HUB_USERNAME}/{job_id}:latest"
            logger.info("Resuming persisted job %s after worker restart.", job_id)
            record_event("info", f"Resuming persisted job {job_id} after worker restart")

            if not self.pull_docker_image(image_name):
                logger.error("Aborting resume of job %s: image pull failed.", job_id)
                self.api.mark_job_failed(
                    job_id, "system",
                    f"Failed to pull Docker image {image_name} while resuming",
                )
                clear_running_job(job_id)
                return False

            self.handle_retry(job_id, image_name, job.get("resume_command"),
                              job.get("command"))
            return True
        except Exception:
            logger.exception("Failed to resume persisted job %s", job_id)
            return False
        finally:
            self._drop_job_log_state(job_id)
            if activated:
                self._unregister_job(job_id)
            self.end_resume(job_id)

    def resume_persisted_job_if_any(self, scan_reserved: bool = False) -> bool:
        """Pick up jobs this worker was running before it died, if the
        scheduler still has them IN_PROGRESS on this worker.

        The scheduler only requeues a job (RETRY_NEEDED) after a worker
        has missed heartbeats for several minutes, so a worker that comes
        back sooner would otherwise leave the job stuck IN_PROGRESS with
        nothing running it. Persisted jobs are kept so a restarted worker
        can resume them. This method resumes all eligible persisted jobs
        up to available capacity so a single call recovers as many as
        possible without blocking the polling loop for the duration of
        a long resume operation.
        """
        if not scan_reserved and not self.try_begin_resume_scan():
            return False

        scan_held = True
        try:
            persisted = []
            try:
                persisted = load_running_jobs()
            except Exception:
                pass
            if not persisted:
                single = load_running_job()
                if single:
                    persisted = [single]

            reserved_job_ids: list[str] = []
            for item in persisted:
                if not isinstance(item, dict):
                    continue
                job_id = item.get("job_id")
                if not job_id:
                    has_valid = any(
                        isinstance(other, dict) and other.get("job_id")
                        for other in persisted
                    )
                    if not has_valid:
                        clear_running_job()
                    continue
                if "/" in str(job_id) or "\\" in str(job_id) or ".." in str(job_id):
                    logger.warning("Dropping unsafe persisted job_id %r", job_id)
                    clear_running_job(job_id)
                    continue

                # Capacity checking and reservation must be one operation;
                # otherwise the polling thread can claim the same last slot.
                if self.begin_resume(job_id):
                    reserved_job_ids.append(job_id)

            # All currently available slots are visible in _resuming now. Once
            # they register (or fail), polling can safely use any capacity left.
            self.end_resume_scan()
            scan_held = False

            if not reserved_job_ids:
                return False

            results: list[bool] = []
            results_lock = threading.Lock()

            def resume_one(job_id: str):
                result = self._resume_reserved_job(job_id)
                with results_lock:
                    results.append(result)

            resume_threads: list[threading.Thread] = []
            for job_id in reserved_job_ids:
                thread = threading.Thread(
                    target=resume_one,
                    args=(job_id,),
                    name=f"resume-job-{job_id}",
                    daemon=True,
                )
                try:
                    thread.start()
                except Exception:
                    logger.exception("Failed to start resume thread for job %s", job_id)
                    self.end_resume(job_id)
                    continue
                resume_threads.append(thread)

            for thread in resume_threads:
                thread.join()

            return any(results)
        finally:
            if scan_held:
                self.end_resume_scan()

    def try_begin_job(self, job_id: str, vram_required: float | None = None) -> bool:
        with self._active_jobs_lock:
            max_jobs = int(runtime_config.get("max_concurrent_jobs") or 2)
            occupied = len(self._active_jobs) + len(self._resuming)
            if (
                self._resume_scan_active
                or occupied >= max_jobs
                or job_id in self._active_jobs
                or job_id in self._pending
                or job_id in self._resuming
            ):
                return False
            self._active_jobs[job_id] = {
                "started_at": time.time(),
                "vram_required": vram_required,
            }
            self._pending.add(job_id)
            return True

    def _register_job(self, job_id: str, vram_required: float | None = None):
        """Track a job as currently being executed."""
        with self._active_jobs_lock:
            self._resuming.discard(job_id)
            if job_id in self._active_jobs:
                self._pending.discard(job_id)
                return
            self._active_jobs[job_id] = {
                "started_at": time.time(),
                "vram_required": vram_required,
            }

    def _unregister_job(self, job_id: str):
        """Remove a job from the active tracking set."""
        with self._active_jobs_lock:
            self._active_jobs.pop(job_id, None)
            self._pending.discard(job_id)

    def _finalize_job_log_state(self, job_id: str | None):
        """Reset per-job log state before a new run starts."""
        self._reset_log_state(job_id)
