from unittest.mock import MagicMock, patch
from managed_worker import ManagedWorker, BatchAPI
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
