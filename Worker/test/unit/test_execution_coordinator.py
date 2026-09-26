import uuid
import pytest
from execution_state import Coordinator


def assignment(kind="interactive_access"):
    return {
        "assignment_id": str(uuid.uuid4()),
        "instance_id": str(uuid.uuid4()),
        "attempt_token": str(uuid.uuid4()),
        "kind": kind,
        "generation": 1 if kind == "interactive_access" else None,
        "lease_seconds": 45,
        "payload": {},
    }


def test_claim_uuid_survives_lost_response_and_interactive_blocks_polling(tmp_path):
    c = Coordinator(tmp_path, clock=lambda: 10)
    c.available()
    request = c.begin_claim()
    assert c.begin_claim() is None
    c.claim_failed()
    assert c.begin_claim() == request
    a = assignment()
    c.accept({"assignment": a}, 10)
    assert not c.may_request_work()
    assert c.authoritative(a["assignment_id"])
    c.paused = True
    assert c.renew(a["assignment_id"], 15, 45, 1)  # Pause never suppresses renewal.
    c.mark_clean(a["assignment_id"])
    assert not c.may_request_work()
    c.released(a["assignment_id"])
    c.paused = False
    assert c.may_request_work()
    c.close()


def test_stopping_new_jobs_persists_and_keeps_active_lease(tmp_path):
    c = Coordinator(tmp_path, clock=lambda: 10)
    c.available()
    c.begin_claim()
    active = assignment("batch_training")
    c.accept({"assignment": active}, 10)
    c.set_accepting_jobs(False)
    assert c.begin_claim() is None
    assert c.renew(active["assignment_id"], 15, 45, 1)
    c.close()

    restarted = Coordinator(tmp_path, clock=lambda: 10)
    restarted.available()
    assert not restarted.accepting_jobs
    assert restarted.begin_claim() is None
    restarted.set_accepting_jobs(True)
    assert restarted.begin_claim() is not None
    restarted.close()


def test_inflight_claim_does_not_start_after_admission_is_disabled(tmp_path):
    c = Coordinator(tmp_path, clock=lambda: 10)
    c.available()
    c.begin_claim()
    c.set_accepting_jobs(False)
    pending = c.accept({"assignment": assignment("batch_training")}, 10)
    assert pending["uncertain"]
    assert not c.authoritative(pending["assignment_id"])
    c.close()


def test_expiry_and_out_of_order_response_cannot_resurrect(tmp_path):
    clock = [10]
    c = Coordinator(tmp_path, clock=lambda: clock[0])
    c.available()
    c.begin_claim()
    a = assignment()
    c.accept({"assignment": a}, 10)
    assert c.renew(a["assignment_id"], 12, 45, 2)
    assert not c.renew(a["assignment_id"], 13, 45, 1)
    clock[0] = 60
    assert not c.renew(a["assignment_id"], 60, 45, 3)
    assert not c.authoritative(a["assignment_id"])
    c.close()


def test_exclusive_acceptance_rejects_local_batch_and_host_double_start(tmp_path):
    c = Coordinator(tmp_path, clock=lambda: 10)
    c.available()
    c.begin_claim()
    c.accept({"assignment": assignment("batch_training")}, 10)
    c.begin_claim()
    a = assignment()
    c.accept({"assignment": a}, 10)
    assert c.mode == "UNCERTAIN" and not c.authoritative(a["assignment_id"])
    with pytest.raises(BlockingIOError):
        Coordinator(tmp_path)
    c.close()


def test_estimation_blocks_claims_and_rejects_overlap(tmp_path):
    c = Coordinator(tmp_path, clock=lambda: 10)
    c.available()
    c.begin_claim()
    estimation = assignment("vram_estimation")
    c.accept({"assignment": estimation}, 10)
    assert not c.may_request_work()

    c.mark_clean(estimation["assignment_id"])
    c.released(estimation["assignment_id"])
    assert c.may_request_work()

    c.begin_claim()
    c.accept({"assignment": assignment("batch_training")}, 10)
    c.begin_claim()
    conflicting = assignment("vram_estimation")
    c.accept({"assignment": conflicting}, 10)
    assert c.mode == "UNCERTAIN"
    assert not c.authoritative(conflicting["assignment_id"])
    c.close()


def test_restart_keeps_cleanup_identity_without_adopting_lease(tmp_path):
    c = Coordinator(tmp_path, clock=lambda: 10)
    c.available()
    c.begin_claim()
    a = assignment()
    c.accept({"assignment": a}, 10)
    old = c.instance_id
    c.close()
    restarted = Coordinator(tmp_path, clock=lambda: 11)
    assert restarted.instance_id != old and restarted.mode == "RECONCILING"
    assert not restarted.authoritative(a["assignment_id"])
    assert restarted.get(a["assignment_id"])["attempt_token"] == a["attempt_token"]
    restarted.close()
