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


def test_scheduler_startup_authentication_errors_are_actionable_and_secret_free():
    from scheduler_protocol import SchedulerRejected

    worker_id = "worker-identity"
    unavailable = scheduler_startup_error(worker_id, SchedulerRejected(503))
    rejected = scheduler_startup_error(worker_id, SchedulerRejected(401))
    assert worker_id in unavailable and "WORKER_CREDENTIALS_FILE" in unavailable
    assert worker_id in rejected and "trailing newline" in rejected
    assert "secret" not in unavailable.lower() and "Bearer " not in unavailable


def test_startup_recovers_one_stale_first_heartbeat_after_reboot(tmp_path):
    """An old instance registration must not turn a reboot into a crash loop."""
    from scheduler_protocol import SchedulerRejected

    coordinator = Coordinator(tmp_path)
    api = MagicMock()
    api.register.return_value = {"reconcile_assignments": []}
    api.heartbeat.side_effect = [
        SchedulerRejected(409),
        {"sequence": 1, "decisions": []},
    ]
    ops = MagicMock()

    with patch("managed_worker.JobExecutor"), patch(
        "managed_worker.get_gpu_info", return_value=("GPU", 0, 0, 0, 0)
    ):
        worker = ManagedWorker(
            "worker", coordinator, api, ops, lambda *_args, **_kwargs: {}
        )
        worker.startup()

    assert api.register.call_count == 2
    assert api.heartbeat.call_count == 2
    assert worker.sequence == 1
    coordinator.close()
