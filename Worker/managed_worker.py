"""Versioned Worker dispatch; long execution never blocks the heartbeat loop."""

from contextlib import suppress
import os
import json
import signal
import threading
import time
from pathlib import Path
from api import SchedulerAPI
from executor import JobExecutor
from execution_state import Coordinator
from scheduler_protocol import ExecutionAPI, Fence, SchedulerRejected
from hardware import get_or_create_worker_id, get_gpu_info, execution_inventory
from telemetry import is_paused, record_heartbeat
import runtime_config
from interactive.docker_ops import DockerOps
from interactive.manager import Manager


class BatchAPI(SchedulerAPI):
    """Preserve batch executor calls; persist results until cleanup/release."""

    def __init__(self, worker_id, coordinator, execution_api):
        super().__init__(worker_id)
        self.coordinator = coordinator
        self.execution_api = execution_api

    def assignment(self, job_id):
        matches = [
            r
            for r in self.coordinator.records()
            if not r.get("released") and r["payload"].get("id") == job_id
        ]
        if len(matches) != 1:
            raise ValueError("No unique fenced batch attempt")
        return matches[0]

    def _result(self, job_id, value):
        self.coordinator.update(
            self.assignment(job_id)["assignment_id"], pending_result=value
        )

    def mark_job_completed(self, job_id):
        self._result(job_id, {"outcome": "completed"})

    def mark_job_failed(self, job_id, failure_type, failure_reason=""):
        self._result(
            job_id,
            {"outcome": "user_failure" if failure_type == "user" else "system_failure"},
        )

    def save_vram_estimation(self, job_id, report):
        self._result(
            job_id,
            {
                "outcome": "estimation",
                "vram_required": float(report["peak_reserved_memory"]),
                "ram_required": float(report["peak_ram_memory"]),
                "step_time": float(report["step_wall_time"]),
            },
        )

    def send_logs(self, job_id, lines):
        if not lines:
            return True
        try:
            record = self.assignment(job_id)
            fence = Fence.from_assignment(record).body()
            batch, size = [], 0
            for line in lines:
                line = line[:4096]
                encoded_size = len(json.dumps(line).encode("ascii")) + 2
                if batch and (len(batch) >= 100 or size + encoded_size > 60000):
                    self.execution_api.call("logs", {**fence, "lines": batch})
                    batch, size = [], 0
                batch.append(line)
                size += encoded_size
            if batch:
                self.execution_api.call("logs", {**fence, "lines": batch})
            return True
        except Exception:
            return False

    def resume_job(self, job_id, gpu_type):
        # Startup reconciles and cleans interrupted attempts before retry. An old
        # batch marker alone can never authorize Docker launch.
        raise RuntimeError("Resume requires durable assignment reconciliation")


class ManagedWorker:
    def __init__(self, worker_id, coordinator, api, ops, inventory=execution_inventory):
        self.worker_id, self.coordinator, self.api, self.ops = (
            worker_id,
            coordinator,
            api,
            ops,
        )
        self.inventory_fn = inventory
        self.batch_api = BatchAPI(worker_id, coordinator, api)
        self.executor = JobExecutor(self.batch_api)
        self.executor.coordinator = coordinator
        self.executor.worker_id = worker_id
        self.executor.managed_assignment = self.batch_api.assignment
        # The executor's slot/resume mutex is the same admission lock.
        self.executor._active_jobs_lock = coordinator.lock
        self.managers = {}
        self.threads = {}
        self.sequence = 0
        self.next_claim_at = 0
        self.preflight_ready = False
        self.stop_event = threading.Event()

    def inventory(self):
        return self.inventory_fn(
            self.coordinator,
            interactive_ready=self.preflight_ready,
            quota_supported=self.preflight_ready,
            available_slots=int(runtime_config.get("max_concurrent_jobs")),
        )

    def startup(self):
        # Resolve a lost-response claim under its original key before abandoning
        # the previous instance. Never invent a new request to cover ambiguity.
        row = self.coordinator.db.execute(
            "SELECT value FROM metadata WHERE key='claim'"
        ).fetchone()
        if row:
            import json

            request = json.loads(row[0])
            self.coordinator.count("claims_sent")
            response = self.api.claim(request)
            if response.get("assignment"):
                record = response["assignment"]
                record.update(
                    containers={}, local_clean=False, released=False, event_sequence=0
                )
                self.coordinator.persist(record)
            with self.coordinator.db:
                self.coordinator.db.execute("DELETE FROM metadata WHERE key='claim'")
        response = self.api.register(
            self.coordinator.instance_id, get_gpu_info()[0], self.inventory()
        )
        known = {r["assignment_id"] for r in self.coordinator.records()}
        if any(
            r["assignment_id"] not in known for r in response["reconcile_assignments"]
        ):
            self.coordinator.mode = "UNCERTAIN"
            raise RuntimeError(
                "Missing journal identity; operator reconciliation required"
            )
        for record in self.coordinator.records():
            if record.get("released"):
                continue
            self.ops.cleanup(record)
            for parent in ("/run/dml-interactive", "/run/dml-interactive-endpoints"):
                directory = Path(parent) / record["assignment_id"]
                if directory.exists():
                    import shutil

                    shutil.rmtree(directory)
            self.coordinator.mark_clean(record["assignment_id"])
            if self.api.cleanup(record)["released"]:
                self.coordinator.released(record["assignment_id"])
        # Batch markers are compatibility records, not reservations. Coordinated
        # drain must handle any unmatched legacy marker before admission.
        from job_state import load_running_jobs, clear_running_job

        for job in load_running_jobs():
            if not any(
                r["payload"].get("id") == job["job_id"]
                for r in self.coordinator.records()
            ):
                self.coordinator.mode = "UNCERTAIN"
                raise RuntimeError("Legacy batch marker requires drain/reconciliation")
            clear_running_job(job["job_id"])
        self.preflight_ready = (
            self.ops.preflight()
            if os.getenv("INTERACTIVE_WORKER_ENABLED", "0") == "1"
            else False
        )
        self.coordinator.available()
        self.heartbeat_once()

    def heartbeat_once(self):
        self.sequence += 1
        self.coordinator.paused = is_paused()
        records = [r for r in self.coordinator.records() if not r.get("released")]
        sent = time.monotonic()
        self.coordinator.count("heartbeats_sent")
        response = self.api.heartbeat(
            {
                "instance_id": self.coordinator.instance_id,
                "sequence": self.sequence,
                "paused": self.coordinator.paused,
                "draining": self.coordinator.draining,
                "inventory": self.inventory(),
                "assignments": [
                    {
                        **Fence.from_assignment(r).body(),
                        "health": (
                            self.managers[r["assignment_id"]].health.get(
                                r["assignment_id"], {}
                            )
                            if r["assignment_id"] in self.managers
                            else {}
                        ),
                    }
                    for r in records
                ],
            }
        )
        for decision in response["decisions"]:
            assignment_id = decision["assignment_id"]
            if decision["action"] == "renew":
                if self.coordinator.renew(
                    assignment_id, sent, decision["lease_seconds"], response["sequence"]
                ):
                    continue
            self.coordinator.update(assignment_id, uncertain=True)
            self.coordinator.mode = "CLEANING"
            if assignment_id in self.managers:
                self.managers[assignment_id].stop()
            else:
                self.ops.cleanup(self.coordinator.get(assignment_id))
        record_heartbeat(True)

    def finish_batch(self, record):
        try:
            self.executor.process_job(record["payload"])
        finally:
            try:
                self.ops.cleanup(self.coordinator.get(record["assignment_id"]))
                self.coordinator.mark_clean(record["assignment_id"])
            except Exception:
                self.coordinator.update(record["assignment_id"], uncertain=True)
                self.coordinator.mode = "UNCERTAIN"

    def maintenance(self):
        for record in self.coordinator.records():
            assignment_id = record["assignment_id"]
            if record.get("released"):
                continue
            thread = self.threads.get(assignment_id)
            if thread and thread.is_alive():
                if not self.coordinator.authoritative(assignment_id):
                    self.coordinator.mode = "CLEANING"
                    if assignment_id in self.managers:
                        self.managers[assignment_id].stop()
                    else:
                        self.ops.cleanup(record)
                continue
            if record.get("local_clean"):
                if record.get("pending_result") and not record.get("result_accepted"):
                    # Expired result cannot complete a job. Cleanup is still
                    # permitted and authoritative Scheduler decides retry.
                    try:
                        self.api.result(record, record["pending_result"])
                        self.coordinator.update(assignment_id, result_accepted=True)
                    except SchedulerRejected as exc:
                        if exc.status != 409:
                            raise
                        # An expired attempt can report only exact cleanup.
                        if time.monotonic() < self.coordinator.deadlines.get(
                            assignment_id, 0
                        ) and not record.get("uncertain"):
                            continue
                if self.api.cleanup(record)["released"]:
                    self.coordinator.released(assignment_id)
                    self.managers.pop(assignment_id, None)
            elif record.get("uncertain"):
                self.ops.cleanup(record)
                self.coordinator.mark_clean(assignment_id)

    def poll_once(self):
        self.coordinator.paused = is_paused()
        self.maintenance()
        if time.monotonic() < self.next_claim_at:
            return
        request = self.coordinator.begin_claim()
        if not request:
            return
        sent = time.monotonic()
        try:
            self.coordinator.count("claims_sent")
            response = self.api.claim(request)
            record = self.coordinator.accept(response, sent)
            self.next_claim_at = time.monotonic() + min(
                30, max(1, response.get("retry_after_seconds", 5))
            )
        except Exception:
            self.coordinator.claim_failed()
            raise
        if not record:
            return
        assignment_id = record["assignment_id"]
        if not self.coordinator.authoritative(assignment_id):
            self.coordinator.update(assignment_id, uncertain=True)
            return
        if record["kind"] == "interactive_access":
            manager = Manager(self.coordinator, self.api, self.ops)
            self.managers[assignment_id] = manager
            target = lambda: manager.run(record)
        else:
            if not self.executor.try_begin_job(
                record["payload"]["id"], record["payload"].get("vram_required")
            ):
                self.coordinator.update(assignment_id, uncertain=True)
                self.coordinator.mode = "UNCERTAIN"
                return
            target = lambda: self.finish_batch(record)
        thread = threading.Thread(
            target=target, name="assignment-" + assignment_id, daemon=True
        )
        self.threads[assignment_id] = thread
        thread.start()

    def heartbeat_loop(self):
        while not self.stop_event.is_set():
            try:
                self.heartbeat_once()
            except Exception:
                record_heartbeat(False, "Scheduler unavailable")
            self.stop_event.wait(runtime_config.get("heartbeat_interval"))

    def shutdown(self):
        self.coordinator.draining = True
        for manager in self.managers.values():
            manager.stop()
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            with suppress(Exception):
                self.maintenance()
            if all(r.get("released") for r in self.coordinator.records()):
                break
            time.sleep(0.2)
        self.stop_event.set()


def _default_state_dir():
    """System dir when writable (service install), else per-user fallback.

    /var/lib/dml-worker is correct for a root/systemd install but fails with
    PermissionError for dev runs as a normal user. Fall back to a user-owned
    directory so `python3 main.py` works without sudo.
    """
    system_default = Path("/var/lib/dml-worker")
    try:
        system_default.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Exist-or-created is not enough; verify we can actually write.
        probe = system_default / ".write-test"
        probe.touch(exist_ok=True)
        probe.unlink(missing_ok=True)
        return str(system_default)
    except OSError:
        pass
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return str(Path(xdg) / "dml-worker")
    return str(Path.home() / ".local" / "share" / "dml-worker")


def scheduler_startup_error(worker_id, error):
    """Return a safe, actionable startup failure without exposing secrets."""
    if error.status == 503:
        return (
            "Worker authentication is unavailable on the Scheduler (503). "
            "Register Worker ID "
            + worker_id
            + " in the Scheduler WORKER_CREDENTIALS_FILE and recreate the API container. "
            "The Worker credential reached the Scheduler; changing this Worker's .env will not repair that map."
        )
    if error.status == 401:
        return (
            "Worker authentication was rejected by the Scheduler (401). "
            "Check that the Scheduler entry for Worker ID "
            + worker_id
            + " contains the same credential, without a trailing newline."
        )
    return "Scheduler rejected Worker startup (HTTP " + str(error.status) + ")."


def run():
    state_dir = os.environ.get("WORKER_STATE_DIR") or _default_state_dir()
    # Default the host lock inside the state dir (user-writable). Only honor
    # an explicit override; the old /run/... default requires root.
    host_lock = os.environ.get("WORKER_HOST_LOCK")
    try:
        coordinator = Coordinator(state_dir, host_lock_path=host_lock)
    except PermissionError as exc:
        raise SystemExit(f"Worker cannot start: {exc}") from exc
    if os.environ.get("WORKER_STATE_DIR") is None and state_dir != "/var/lib/dml-worker":
        print(f"WORKER_STATE_DIR not set; using user-writable fallback: {state_dir}")
    scheduler_url = os.environ.get("SCHEDULER_URL")
    cred_file = os.environ.get("WORKER_SERVICE_CREDENTIAL_FILE")
    missing = [k for k, v in (("SCHEDULER_URL", scheduler_url), ("WORKER_SERVICE_CREDENTIAL_FILE", cred_file)) if not v]
    if missing:
        raise SystemExit(
            f"Worker cannot start: missing required env: {', '.join(missing)}. "
            f"See Worker/.env.example."
        )
    worker_id = get_or_create_worker_id()
    api = ExecutionAPI(scheduler_url, cred_file)
    worker = ManagedWorker(
        worker_id, coordinator, api, DockerOps(coordinator, worker_id)
    )
    signal.signal(signal.SIGTERM, lambda *_: setattr(coordinator, "draining", True))
    signal.signal(signal.SIGINT, lambda *_: setattr(coordinator, "draining", True))
    try:
        try:
            worker.startup()
        except SchedulerRejected as exc:
            raise SystemExit(scheduler_startup_error(worker_id, exc)) from None
        heartbeat = threading.Thread(target=worker.heartbeat_loop, daemon=True)
        heartbeat.start()
        import server

        server.run_in_thread(
            os.getenv("WORKER_API_HOST", "127.0.0.1"),
            int(os.getenv("WORKER_API_PORT", "8600")),
        )
        while not coordinator.draining:
            with suppress(Exception):
                worker.poll_once()
            worker.stop_event.wait(min(1, runtime_config.get("job_poll_interval")))
    finally:
        worker.shutdown()
        coordinator.close()
