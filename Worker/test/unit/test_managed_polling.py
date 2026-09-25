from unittest.mock import MagicMock, patch
from managed_worker import ManagedWorker, BatchAPI, scheduler_startup_error
import json
from execution_state import Coordinator
from test.unit.test_execution_coordinator import assignment


def test_interactive_reservation_stops_claim_resume_and_keeps_heartbeats(
    tmp_path, reset_telemetry
):
    c = Coordinator(tmp_path)
    c.available()
    a = assignment()
    a["instance_id"] = c.instance_id
    api = MagicMock()
    api.claim.return_value = {"assignment": a}
    ops = MagicMock()

    def inventory(*args, **kwargs):
        return {"mode": c.mode}

    with patch("managed_worker.JobExecutor") as executor:
        worker = ManagedWorker("worker", c, api, ops, inventory)
        c.begin_claim()
        import time

        c.accept({"assignment": a}, time.monotonic())
        worker.managers[a["assignment_id"]] = MagicMock(
            health={
                a["assignment_id"]: {
                    "workload": True,
                    "broker": True,
                    "access": True,
                    "endpoint": True,
                }
            }
        )
        running = MagicMock()
        running.is_alive.return_value = True
        worker.threads[a["assignment_id"]] = running
        api.heartbeat.side_effect = lambda body: {
            "sequence": body["sequence"],
            "decisions": [
                {
                    "assignment_id": a["assignment_id"],
                    "action": "renew",
                    "lease_seconds": 45,
                }
            ],
        }
        for phase in ("PULLING", "STARTING", "READY", "STOPPING"):
            c.paused = True
            worker.poll_once()
            worker.heartbeat_once()
        api.claim.assert_not_called()
        executor.return_value.resume_persisted_job_if_any.assert_not_called()
        assert api.heartbeat.call_count == 4
        assert c.mode in ("INTERACTIVE_RESERVED", "INTERACTIVE_ACTIVE")
    c.close()


def test_estimation_stops_worker_polling_until_release(tmp_path, reset_telemetry):
    c = Coordinator(tmp_path)
    c.available()
    estimation = assignment("vram_estimation")
    estimation["instance_id"] = c.instance_id
    c.begin_claim()
    import time

    c.accept({"assignment": estimation}, time.monotonic())
    api = MagicMock()
    api.claim.return_value = {"assignment": None, "retry_after_seconds": 5}
    with patch("managed_worker.JobExecutor"):
        worker = ManagedWorker(
            "worker", c, api, MagicMock(), lambda *args, **kwargs: {}
        )
        worker.poll_once()
        api.claim.assert_not_called()

        c.mark_clean(estimation["assignment_id"])
        c.released(estimation["assignment_id"])
        worker.poll_once()
        api.claim.assert_called_once()
    c.close()


def test_network_failure_cannot_release_pending_result(tmp_path):
    c = Coordinator(tmp_path)
    c.available()
    a = assignment("batch_training")
    a["payload"] = {"id": "job"}
    c.begin_claim()
    import time

    c.accept({"assignment": a}, time.monotonic())
    c.update(a["assignment_id"], pending_result={"outcome": "completed"})
    c.mark_clean(a["assignment_id"])
    api = MagicMock()
    api.result.side_effect = ConnectionError("unavailable")
    with patch("managed_worker.JobExecutor"):
        worker = ManagedWorker(
            "worker", c, api, MagicMock(), lambda *args, **kwargs: {}
        )
        import pytest

        with pytest.raises(ConnectionError):
            worker.maintenance()
        api.cleanup.assert_not_called()
        assert not c.get(a["assignment_id"])["released"]
    c.close()


def test_batch_result_stays_bound_to_original_attempt_after_reassignment(tmp_path):
    import time

    c = Coordinator(tmp_path)
    c.available()
    first = assignment("batch_training")
    first["payload"] = {"id": "job"}
    c.begin_claim()
    c.accept({"assignment": first}, time.monotonic())
    transport = MagicMock()
    api = BatchAPI("worker", c, transport)

    with api.bound(first):
        c.mark_clean(first["assignment_id"])
        c.released(first["assignment_id"])
        second = assignment("batch_training")
        second["payload"] = {"id": "job"}
        c.persist({**second, "local_clean": False, "released": False})
        api.mark_job_completed("job")

    assert c.get(first["assignment_id"])["pending_result"] == {"outcome": "completed"}
    assert "pending_result" not in c.get(second["assignment_id"])
    c.close()


def test_startup_replays_persisted_batch_result_after_local_cleanup(
    tmp_path, reset_telemetry
):
    import time

    c = Coordinator(tmp_path)
    c.available()
    attempt = assignment("batch_training")
    attempt["payload"] = {"id": "job"}
    c.begin_claim()
    c.accept({"assignment": attempt}, time.monotonic())
    c.update(attempt["assignment_id"], pending_result={"outcome": "completed"})

    api = MagicMock()
    api.register.return_value = {"reconcile_assignments": [attempt]}
    api.cleanup.return_value = {"released": True}
    api.heartbeat.return_value = {"sequence": 1, "decisions": []}
    ops = MagicMock()
    ops.preflight.return_value = False
    with patch("managed_worker.JobExecutor"), patch(
        "job_state.load_running_jobs", return_value=[]
    ):
        worker = ManagedWorker("worker", c, api, ops, lambda *args, **kwargs: {})
        worker.startup()

    assert c.get(attempt["assignment_id"])["released"]
    assert c.get(attempt["assignment_id"])["result_accepted"]
    api.result.assert_called_once()
    assert api.result.call_args.args[1] == {"outcome": "completed"}
    api.cleanup.assert_called_once()
    c.close()


def test_cleanup_retries_persisted_interactive_failure_code():
    from scheduler_protocol import ExecutionAPI

    a = assignment("interactive_access")
    a["runtime_failure_code"] = "START_FAILED"
    api = ExecutionAPI.__new__(ExecutionAPI)
    api.call = MagicMock(return_value={"released": True})

    api.cleanup(a)

    assert api.call.call_args.args == (
        "cleanup",
        {
            "instance_id": a["instance_id"],
            "assignment_id": a["assignment_id"],
            "attempt_token": a["attempt_token"],
            "generation": a["generation"],
            "failure_code": "START_FAILED",
        },
    )


def test_fenced_batch_logs_fit_request_limit_even_with_unicode():
    record = assignment("batch_training")
    record["payload"] = {"id": "job"}
    coordinator = MagicMock()
    coordinator.records.return_value = [record]
    api = MagicMock()
    lines = ["😀" * 4096] * 5 + ["short line"] * 200
    assert BatchAPI("worker", coordinator, api).send_logs("job", lines)
    delivered = []
    for call in api.call.call_args_list:
        operation, body = call.args
        assert operation == "logs" and body["assignment_id"] == record["assignment_id"]
        assert len(json.dumps(body).encode("ascii")) <= 65536
        assert len(body["lines"]) <= 100
        delivered.extend(body["lines"])
    assert delivered == lines


def test_startup_closes_assignment_the_scheduler_no_longer_holds(
    tmp_path, reset_telemetry
):
    """Regression: an orphaned journal row must not brick every Worker start.

    A locally persisted, fenced assignment that the control plane no longer
    serves makes ``api.cleanup`` answer 409 "Stale assignment". Startup used to
    propagate that as fatal, so systemd restarted the Worker forever and the
    Scheduler never received a heartbeat.
    """
    from scheduler_protocol import SchedulerRejected
    import time

    c = Coordinator(tmp_path)
    c.available()
    orphan = assignment("batch_training")
    orphan["instance_id"] = c.instance_id
    orphan["payload"] = {"id": "job"}
    c.begin_claim()
    c.accept({"assignment": orphan}, time.monotonic())
    c.mark_clean(orphan["assignment_id"])

    api = MagicMock()
    api.register.return_value = {"reconcile_assignments": []}
    api.cleanup.side_effect = SchedulerRejected(409)
    api.heartbeat.return_value = {"sequence": 1, "decisions": []}
    ops = MagicMock()
    ops.preflight.return_value = False

    with patch("managed_worker.JobExecutor"), patch(
        "job_state.load_running_jobs", return_value=[]
    ):
        worker = ManagedWorker(
            "worker", c, api, ops, lambda *args, **kwargs: {"mode": c.mode}
        )
        worker.startup()

    assert c.get(orphan["assignment_id"])["released"] is True
    assert c.mode == "AVAILABLE"
    api.heartbeat.assert_called_once()
    c.close()


def test_startup_does_not_swallow_unexpected_scheduler_rejections(
    tmp_path, reset_telemetry
):
    """Only 409 (assignment gone) is tolerated during cleanup replay."""
    from scheduler_protocol import SchedulerRejected
    import pytest
    import time

    c = Coordinator(tmp_path)
    c.available()
    orphan = assignment("batch_training")
    orphan["instance_id"] = c.instance_id
    orphan["payload"] = {"id": "job"}
    c.begin_claim()
    c.accept({"assignment": orphan}, time.monotonic())
    c.mark_clean(orphan["assignment_id"])

    api = MagicMock()
    api.register.return_value = {"reconcile_assignments": []}
    api.cleanup.side_effect = SchedulerRejected(401)

    with patch("managed_worker.JobExecutor"), patch(
        "job_state.load_running_jobs", return_value=[]
    ):
        worker = ManagedWorker(
            "worker", c, api, MagicMock(), lambda *args, **kwargs: {"mode": c.mode}
        )
        with pytest.raises(SchedulerRejected):
            worker.startup()
    assert not c.get(orphan["assignment_id"])["released"]
    c.close()


def test_scheduler_startup_authentication_errors_are_actionable_and_secret_free():
    from scheduler_protocol import SchedulerRejected

    worker_id = "worker-identity"
    unavailable = scheduler_startup_error(worker_id, SchedulerRejected(503))
    rejected = scheduler_startup_error(worker_id, SchedulerRejected(401))
    conflicted = scheduler_startup_error(worker_id, SchedulerRejected(409))
    assert worker_id in unavailable and "WORKER_CREDENTIALS_FILE" in unavailable
    assert worker_id in rejected and "trailing newline" in rejected
    assert "409" in conflicted and "WORKER_STATE_DIR" in conflicted
    assert "secret" not in unavailable.lower() and "Bearer " not in unavailable
