import os
import json
import shlex
import shutil
import logging
import subprocess
import tempfile
import time
import docker

from config import (
    OUTPUT_DIR, VRAM_ESTIMATION_SCRIPT, DOCKER_HUB_USERNAME,
    CONTAINER_OUTPUT_MOUNT,
)
from api import SchedulerAPI
from object_store import ObjectStore
from output_monitor import OutputFileMonitor
from telemetry import record_job, record_event
from checkpoint import checkpoint_manager, checkpointing_enabled
from job_registry import JobEntry, registry
import runtime_config

logger = logging.getLogger("executor")

class JobExecutor:
    def __init__(self, api: SchedulerAPI):
        self.api = api
        try:
            self.docker_client = docker.from_env()
        except Exception as e:
            logger.error("Failed to connect to Docker daemon: %s", e)

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
            self.docker_client.images.pull(image_name)
            logger.info("Successfully pulled image: %s", image_name)
            return True
        except Exception as e:
            logger.error("Failed to pull image %s: %s", image_name, e)
            return False

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
            return

        with tempfile.TemporaryDirectory(prefix=f"vram_{job_id}_") as report_dir:
            report_path = os.path.join(report_dir, "report.json")
            cmd = [
                "docker", "run", "--rm", "--gpus", "all",
                "-v", f"{VRAM_ESTIMATION_SCRIPT}:/vram_estimation.py:ro",
                "-v", f"{report_dir}:/report",
                "--entrypoint", "python", image_name,
                "/vram_estimation.py", "--output", "/report/report.json", *target_command,
            ]
            
            logger.info("Running VRAM estimation for job %s.", job_id)
            record_event("info", f"Job {job_id} VRAM estimation started")
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error("VRAM estimation failed for job %s: %s", job_id, result.stderr.strip())
                self._record_job(job_id, image_name, "vram_estimation", "failed", started_at)
                return
            
            try:
                with open(report_path, encoding="utf-8") as report_file:
                    report = json.load(report_file)
                if report.get("step_wall_time") is None:
                    raise ValueError("No optimizer steps were observed")
            except (OSError, ValueError, json.JSONDecodeError) as e:
                logger.error("Invalid VRAM estimation report for job %s: %s", job_id, e)
                return

        self.api.save_vram_estimation(job_id, report)
        self._record_job(
            job_id, image_name, "vram_estimation", "completed",
            started_at, round(report.get("peak_reserved_memory", 0.0), 2),
        )

    def handle_training(self, job_id: str, image_name: str,
                        checkpoint_interval: float | None = None):
        started_at = time.time()
        logger.info("Training job received for job %s.", job_id)
        record_event("info", f"Job {job_id} training started")

        job_output_dir = os.path.join(OUTPUT_DIR, job_id)
        os.makedirs(job_output_dir, exist_ok=True)

        store = ObjectStore()
        monitor = OutputFileMonitor(job_id, job_output_dir, store)
        monitor.start()

        self._build_log_base = None
        self._last_log_upload = None
        self._log_push_buffer = []
        self._last_log_push = None
        self._job_log_buffer: list[str] = []
        self._last_log_ts = None

        if checkpointing_enabled():
            self._run_training_checkpointable(
                job_id, image_name, job_output_dir, store, monitor,
                started_at, checkpoint_interval,
            )
        else:
            self._run_training_legacy(
                job_id, image_name, job_output_dir, store, monitor, started_at,
            )

    def _run_training_legacy(self, job_id: str, image_name: str,
                             job_output_dir: str, store: ObjectStore,
                             monitor: OutputFileMonitor, started_at: float):
        cmd = [
            "docker", "run", "--rm", "--gpus", "all",
            "-v", f"{job_output_dir}:{CONTAINER_OUTPUT_MOUNT}",
            image_name,
        ]
        logger.info("Running container: %s", " ".join(cmd))

        success = False
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )

            for line in iter(proc.stdout.readline, ""):
                line = line.rstrip("\n")
                self._job_log_buffer.append(line)
                self._log_push_buffer.append(line)
                self._flush_log_push(job_id)
                logger.info("[job %s] %s", job_id, line)

                if time.monotonic() - (self._last_log_upload or 0) >= runtime_config.get("log_upload_interval"):
                    self._append_build_log(job_id, store, self._job_log_buffer)

            proc.wait()
            success = proc.returncode == 0
        except Exception as e:
            logger.error("Execution error for job %s: %s", job_id, e)

        self._flush_log_push(job_id, force=True)
        self._append_build_log(job_id, store, self._job_log_buffer, force=True)
        monitor.stop()
        self._finalize_job(job_id, image_name, job_output_dir, started_at, success)

    def _run_training_checkpointable(self, job_id: str, image_name: str,
                                     job_output_dir: str, store: ObjectStore,
                                     monitor: OutputFileMonitor,
                                     started_at: float,
                                     checkpoint_interval: float | None):
        container_name = f"dmlts-{job_id}"
        entry = JobEntry(
            job_id, container_name, image_name,
            checkpoint_manager.job_checkpoint_dir(job_id),
        )
        entry.output_dir = job_output_dir
        entry.store = store
        entry.monitor = monitor
        if checkpoint_interval is not None:
            entry.checkpoint_interval = float(checkpoint_interval)
        registry.add(entry)

        success = False
        try:
            self._cleanup_container(container_name)
            run_cmd = [
                "docker", "run", "-d", "--name", container_name,
                "--gpus", "all",
                "-v", f"{job_output_dir}:{CONTAINER_OUTPUT_MOUNT}",
                image_name,
            ]
            logger.info("Starting container: %s", " ".join(run_cmd))
            res = subprocess.run(run_cmd, capture_output=True, text=True)
            if res.returncode != 0:
                logger.error("Failed to start container for job %s: %s",
                             job_id, res.stderr.strip())
                self._record_job(job_id, image_name, "training", "failed", started_at)
                return
            record_event("info", f"Job {job_id} container started ({container_name})")
            success = self._monitor_container(entry)
        except Exception as e:
            logger.error("Execution error for job %s: %s", job_id, e)
        finally:
            self._flush_log_push(job_id, force=True)
            self._append_build_log(job_id, store, self._job_log_buffer, force=True)
            monitor.stop()
            self._cleanup_container(container_name)
            registry.remove(job_id)

        self._finalize_job(job_id, image_name, job_output_dir, started_at, success)

    def _monitor_container(self, entry: JobEntry) -> bool:
        """Follow logs across pause/restore cycles until the job terminates."""
        while True:
            state = checkpoint_manager.container_state(entry.container)
            if state == "running":
                self._follow_container_logs(entry)
                continue
            if state == "exited":
                if self._is_paused_exit(entry):
                    time.sleep(1.0)
                    continue
                exit_code = checkpoint_manager.container_exit_code(entry.container)
                logger.info("Container %s exited with code %s",
                            entry.container, exit_code)
                return exit_code == 0
            if state == "missing":
                logger.error("Container %s is missing", entry.container)
                return False
            time.sleep(1.0)

    def _follow_container_logs(self, entry: JobEntry):
        cmd = ["docker", "logs", "-f", "-t"]
        if self._last_log_ts is not None:
            cmd += ["--since", self._last_log_ts]
        cmd.append(entry.container)
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
        except Exception as e:
            logger.error("Failed to follow logs for %s: %s", entry.container, e)
            return

        for raw in iter(proc.stdout.readline, ""):
            line = raw.rstrip("\n")
            ts, content = self._split_log_line(line)
            if ts is not None:
                self._last_log_ts = ts
            else:
                content = line
            self._job_log_buffer.append(content)
            self._log_push_buffer.append(content)
            self._flush_log_push(entry.job_id)
            logger.info("[job %s] %s", entry.job_id, content)

            if time.monotonic() - (self._last_log_upload or 0) >= runtime_config.get("log_upload_interval"):
                self._append_build_log(entry.job_id, entry.store, self._job_log_buffer)
        proc.wait()

    @staticmethod
    def _split_log_line(line: str) -> tuple[str | None, str]:
        ts, sep, rest = line.partition(" ")
        if not sep or not ts.endswith("Z"):
            return None, line
        _, _, rest = rest.partition(" ")
        _, sep, content = rest.partition(" ")
        return ts, content if sep else ""

    def _is_paused_exit(self, entry: JobEntry) -> bool:
        with entry.lock:
            return entry.state in ("paused", "checkpointing", "restoring")

    @staticmethod
    def _cleanup_container(container_name: str):
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True, text=True,
        )

    def _finalize_job(self, job_id: str, image_name: str, job_output_dir: str,
                      started_at: float, success: bool):
        if success:
            logger.info("Job %s completed successfully.", job_id)
            self.api.mark_job_completed(job_id)
            shutil.rmtree(job_output_dir, ignore_errors=True)
            logger.info("Deleted output directory for job %s.", job_id)
            self._record_job(job_id, image_name, "training", "completed", started_at)
        else:
            logger.error(
                "Job %s failed; uploading all output to object store.", job_id
            )
            monitor.flush()
            shutil.rmtree(job_output_dir, ignore_errors=True)
            logger.info("Deleted output directory for job %s.", job_id)
            self._record_job(job_id, image_name, "training", "failed", started_at)

    def _flush_log_push(self, job_id: str, force: bool = False):
        if not self._log_push_buffer:
            return

        now = time.monotonic()
        if (
            not force
            and self._last_log_push is not None
            and (now - self._last_log_push) < runtime_config.get("log_push_interval")
        ):
            return

        lines = self._log_push_buffer
        self._log_push_buffer = []
        self.api.send_logs(job_id, lines)
        self._last_log_push = now

    def _append_build_log(
        self, job_id: str, store: ObjectStore, log_buffer: list[str], force: bool = False
    ):
        if not log_buffer:
            return

        now = time.monotonic()
        if (
            not force
            and self._last_log_upload is not None
            and (now - self._last_log_upload) < runtime_config.get("log_upload_interval")
        ):
            return

        if self._build_log_base is None:
            existing = store.download(f"{job_id}/build.log")
            self._build_log_base = (
                existing.decode("utf-8", errors="replace") if existing else ""
            )

        content = self._build_log_base
        if content and not content.endswith("\n"):
            content += "\n"
        content += "\n".join(log_buffer) + "\n"

        if store.upload_bytes(
            f"{job_id}/build.log", content.encode("utf-8"), "text/plain"
        ):
            self._last_log_upload = now

    def process_job(self, job: dict):
        job_id = job.get("job_id") or job.get("id")
        flag = job.get("flag", "training")
        image_name = f"{DOCKER_HUB_USERNAME}/{job_id}:latest"

        if not self.pull_docker_image(image_name):
            logger.error("Aborting job %s: image pull failed.", job_id)
            return

        if flag == "vram_estimation":
            self.handle_vram_estimation(job_id, image_name, job.get("command", ""))
        elif flag == "training":
            self.handle_training(
                job_id, image_name,
                checkpoint_interval=job.get("checkpoint_interval"),
            )
        elif flag == "retry":
            logger.info("Retry job received for job %s.", job_id)
        else:
            logger.warning("Unknown job flag '%s' for job %s.", flag, job_id)