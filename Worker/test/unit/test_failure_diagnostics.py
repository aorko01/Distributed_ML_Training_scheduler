import json
import os
import stat
from unittest.mock import MagicMock

from interactive.failure_diagnostics import (
    collect_failure_diagnostics,
    dump_dir,
    short_detail,
)


def _container(name, component, state, logs):
    c = MagicMock()
    c.id = name + "-id"
    c.labels = {"dml.component": component}
    c.attrs = {"State": state}
    c.logs.return_value = logs
    return c


def test_collect_persists_logs_and_sanitized_state(tmp_path):
    access = _container(
        "access",
        "access",
        {
            "Status": "exited",
            "Running": False,
            "ExitCode": 1,
            "Error": "",
            "OOMKilled": False,
            "StartedAt": "2026-09-20T02:33:21Z",
            "FinishedAt": "2026-09-20T02:33:21Z",
            "Pid": 123,
        },
        b"PermissionError: [Errno 13] Permission denied: '/run/x/broker.sock'\n",
    )
    # Config/Env must never leak even if the SDK exposes them.
    access.attrs["Config"] = {"Env": ["SECRET=topsecret"]}
    sidecar = _container(
        "sidecar", "sidecar", {"Status": "running", "Running": True}, b"tailscaled up\n"
    )
    client = MagicMock()
    client.containers.get.side_effect = lambda cid: {
        "access-id": access,
        "sidecar-id": sidecar,
    }[cid]
    client.containers.list.return_value = [access, sidecar]

    record = {"assignment_id": "aid-1", "containers": {"access": "access-id"}}
    summary = collect_failure_diagnostics(record, client, tmp_path, "backend-wait", "START_FAILED")

    assert summary["stage"] == "backend-wait"
    assert summary["components"]["access"]["state"]["ExitCode"] == 1
    # Label fallback discovers the sidecar, which was absent from the journal.
    assert summary["components"]["sidecar"]["state"]["Status"] == "running"

    directory = dump_dir(tmp_path, "aid-1")
    assert summary["dump_dir"] == str(directory)
    assert (directory / "access.log").read_text().startswith("PermissionError")
    persisted = json.loads((directory / "summary.json").read_text())
    assert "SECRET" not in (directory / "summary.json").read_text()
    assert persisted["components"]["access"]["state"]["ExitCode"] == 1
    assert stat.S_IMODE(os.stat(directory).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(directory / "access.log").st_mode) == 0o600

    detail = short_detail("backend-wait", summary)
    assert detail.startswith("backend-wait")
    assert "access=exited(1)" in detail


def test_collect_never_raises_without_docker_or_state_dir():
    client = MagicMock()
    client.containers.list.side_effect = RuntimeError("docker down")
    record = {"assignment_id": "aid-2", "containers": {"workload": "missing"}}
    client.containers.get.side_effect = RuntimeError("gone")
    summary = collect_failure_diagnostics(record, client, None, "pull", "PULL_FAILED")
    assert summary["components"]["workload"]["error"] == "RuntimeError"
    assert "dump_dir" not in summary
    assert short_detail("pull", summary).startswith("pull")


def test_collect_chmod_survives_restrictive_umask(tmp_path):
    old = os.umask(0o077)
    try:
        summary = collect_failure_diagnostics(
            {"assignment_id": "aid-3", "containers": {}},
            MagicMock(containers=MagicMock(list=MagicMock(return_value=[]))),
            tmp_path,
            "reserve",
            "START_FAILED",
        )
    finally:
        os.umask(old)
    directory = dump_dir(tmp_path, "aid-3")
    assert (directory / "summary.json").exists()
    assert stat.S_IMODE(os.stat(directory).st_mode) == 0o700
    assert summary["dump_dir"] == str(directory)
