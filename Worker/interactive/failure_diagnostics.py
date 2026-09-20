"""Best-effort persistence of failed interactive container diagnostics.

Keeping a failed container (``INTERACTIVE_CLEANUP_ON_FAILURE`` unset) preserves
``docker logs`` only while the container object still exists. A later prune,
daemon GC, or operator cleanup removes the evidence, and the ephemeral
``/run/dml-interactive/<assignment>/`` directory is always removed by the
Manager's finally-block. This module snapshots, at failure time:

- ``docker logs`` tail (300 lines, capped) per component, and
- sanitized ``docker inspect`` State (Status/ExitCode/Error/OOMKilled only;
  never Config/Env/Mounts/credentials), plus the Manager stage that failed.

Dumps stay on the host under ``<WORKER_STATE_DIR>/interactive-failures/``
(mode 0700, files 0600) and are never sent to the Scheduler. Collection never
raises; every step is individually guarded so diagnostics cannot mask the
original failure.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger("docker_ops")

LOG_TAIL_LINES = 300
LOG_TAIL_BYTES = 65536
SUMMARY_EXCERPT_BYTES = 2048
DETAIL_MAX = 256

# Sanitized inspect subset. Explicitly excludes Config (Env), Mounts,
# NetworkSettings, and anything else that could carry secrets.
STATE_FIELDS = (
    "Status",
    "Running",
    "ExitCode",
    "Error",
    "OOMKilled",
    "StartedAt",
    "FinishedAt",
)

ASSIGNMENT_LABEL = "dml.assignment"


def dump_dir(state_dir, assignment_id):
    return Path(state_dir) / "interactive-failures" / assignment_id


def short_detail(stage, summary):
    """One-line journal detail, e.g. ``backend-wait access=exited(1)``."""
    parts = []
    for component, entry in (summary.get("components") or {}).items():
        state = entry.get("state") or {}
        status = state.get("Status", "unknown")
        if status == "exited":
            parts.append("%s=%s(%s)" % (component, status, state.get("ExitCode")))
        else:
            parts.append("%s=%s" % (component, status))
        if entry.get("error"):
            parts.append("%s!%s" % (component, entry["error"]))
    text = ("%s %s" % (stage, " ".join(parts))).strip()
    return text[:DETAIL_MAX]


def _container_ids(record, client):
    """Exact journal IDs first, label listing as fallback. Never raises."""
    ids = dict(record.get("containers") or {})
    try:
        objects = client.containers.list(
            all=True,
            filters={"label": [ASSIGNMENT_LABEL + "=" + record["assignment_id"]]},
        )
        for container in objects:
            try:
                component = (container.labels or {}).get("dml.component")
            except Exception:
                component = None
            if component and component not in ids:
                ids[component] = container.id
    except Exception:
        logger.debug("failure diagnostics label fallback failed", exc_info=True)
    return ids


def _snapshot(client, container_id):
    entry = {"container_id": container_id}
    try:
        container = client.containers.get(container_id)
    except Exception as exc:
        entry["error"] = type(exc).__name__
        return entry
    try:
        with _suppress():
            container.reload()
        state = (container.attrs or {}).get("State") or {}
        entry["state"] = {
            key: state.get(key) for key in STATE_FIELDS if key in state
        }
    except Exception as exc:
        entry["error"] = type(exc).__name__
    try:
        raw = container.logs(tail=LOG_TAIL_LINES)
        if isinstance(raw, bytes):
            text = raw.decode("utf-8", errors="replace")
        else:
            text = str(raw)
        entry["log_tail"] = text[-LOG_TAIL_BYTES:]
        entry["log_bytes"] = len(text)
    except Exception as exc:
        entry["log_error"] = type(exc).__name__
    return entry


class _suppress:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return True


def collect_failure_diagnostics(record, client, state_dir, stage, code):
    """Snapshot logs/inspect for a failed assignment. Never raises.

    Returns a summary dict (also logged by the caller). ``record`` needs only
    ``assignment_id`` and optionally ``containers``.
    """
    assignment_id = record.get("assignment_id", "unknown")
    summary = {
        "assignment_id": assignment_id,
        "stage": stage,
        "code": code,
        "components": {},
    }
    try:
        for component, container_id in _container_ids(record, client).items():
            try:
                summary["components"][component] = _snapshot(client, container_id)
            except Exception as exc:  # pragma: no cover - defensive
                summary["components"][component] = {"error": type(exc).__name__}
    except Exception as exc:  # pragma: no cover - defensive
        summary["collector_error"] = type(exc).__name__
    try:
        if state_dir:
            directory = dump_dir(state_dir, assignment_id)
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            # mkdir mode is masked by the process umask (systemd UMask=0077
            # yields 0700 anyway, but enforce exact perms either way).
            os.chmod(directory, 0o700)
            persisted = {
                "assignment_id": assignment_id,
                "stage": stage,
                "code": code,
                "components": {},
            }
            for component, entry in summary["components"].items():
                safe = "".join(
                    ch if ch.isalnum() or ch in ("-", "_") else "_"
                    for ch in component
                )[:64]
                tail = entry.get("log_tail", "")
                log_file = directory / (safe + ".log")
                log_file.write_text(tail, encoding="utf-8", errors="replace")
                os.chmod(log_file, 0o600)
                persisted["components"][component] = {
                    key: value
                    for key, value in entry.items()
                    if key != "log_tail"
                }
                persisted["components"][component]["log_excerpt"] = tail[
                    -SUMMARY_EXCERPT_BYTES:
                ]
                persisted["components"][component]["log_file"] = str(log_file)
            summary_file = directory / "summary.json"
            import json as _json

            summary_file.write_text(
                _json.dumps(persisted, indent=2, default=str)[:131072]
            )
            os.chmod(summary_file, 0o600)
            summary["dump_dir"] = str(directory)
    except Exception:
        logger.debug(
            "failure diagnostics persistence failed assignment_id=%s",
            assignment_id,
            exc_info=True,
        )
    return summary
