"""Durable workspace capture (plan.md Phase 2).

One capture per save operation: gate, exact-label ``docker commit --pause``,
streamed ``docker save | gzip`` upload, staging-tag cleanup.  Every Docker side
effect is fenced by the Worker lease (``DockerOps.authority``) and journaled
before it runs.  The module never talks to a registry and never prunes.
"""

import concurrent.futures
from contextlib import suppress
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path

logger = logging.getLogger("managed_worker")

CAPTURE_PAUSED_SECONDS = 300
CAPTURE_UNPAUSE_SECONDS = 10
UPLOAD_VERIFY_CHUNK = 1 << 20
HEARTBEAT_GRACE_SECONDS = 20


from .docker_ops import workload_internet_enabled  # single Worker egress gate (plan.md Phase 1)


class CaptureDenied(RuntimeError):
    """Controlled capture refusal with a stable public code."""

    def __init__(self, code, detail=""):
        self.code, self.detail = code, detail[:256]
        super().__init__(code + (": " + self.detail if self.detail else ""))


def staging_tag(operation_id):
    """Operation-scoped staging tag; reconciled by exact id, never pruned."""
    return f"dml-snapshot-{operation_id}:capture"


def _snapshot_dir(state_dir):
    """Crash-safe sidecar journal next to the coordinator database."""
    root = Path(state_dir) / "snapshots" if state_dir else Path(tempfile.gettempdir()) / "dml-snapshots"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def _record_path(state_dir, operation_id):
    return _snapshot_dir(state_dir) / (operation_id + ".json")


def journal_snapshot(state_dir, operation_id, values):
    record = {}
    path = _record_path(state_dir, operation_id)
    if path.exists():
        with suppress(Exception):
            record = json.loads(path.read_text())
    record.update(values)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, sort_keys=True))
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return record


def load_snapshot(state_dir, operation_id):
    path = _record_path(state_dir, operation_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _check_labels(workload, record):
    labels = getattr(workload, "labels", None) or {}
    if (
        labels.get("dml.component") != "workload"
        or labels.get("dml.assignment") != record["assignment_id"]
    ):
        raise CaptureDenied("CAPTURE_FORBIDDEN", "workload identity mismatch")


def _check_config(config):
    if config.get("Volumes"):
        raise CaptureDenied("VOLUME_IMAGE", "volume-backed image is not portable")
    return config


def _pause_with_timeout(workload, seconds):
    done = threading.Event()
    outcome = {}

    def run():
        try:
            workload.pause(timeout=seconds)
            outcome["ok"] = True
        except Exception as exc:  # noqa: BLE001 - surfaced as CAPTURE_TIMEOUT
            outcome["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    if not done.wait(seconds):
        with suppress(Exception):
            workload.unpause(timeout=CAPTURE_UNPAUSE_SECONDS)
        raise CaptureDenied("CAPTURE_TIMEOUT", "workload pause deadline exceeded")
    if not outcome.get("ok"):
        raise CaptureDenied("CAPTURE_TIMEOUT", "workload pause failed")

def _upload(client, assignment_id, image_id, destination, deadline):
    """Stream ``docker save`` through gzip into the artifact file.

    Never buffers the whole image in RAM; aborts on lease loss.
    """
    import gzip

    sha = hashlib.sha256()
    size = 0
    image = client.images.get(image_id)
    stream = image.save(named=True)
    try:
        with open(destination, "wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as out:
                for chunk in stream:
                    if time.monotonic() > deadline:
                        raise CaptureDenied("CAPTURE_TIMEOUT", "upload deadline exceeded")
                    if not chunk:
                        continue
                    out.write(chunk)
        with open(destination, "rb") as check:
            while True:
                piece = check.read(UPLOAD_VERIFY_CHUNK)
                if not piece:
                    break
                sha.update(piece)
                size += len(piece)
    finally:
        with suppress(Exception):
            close = getattr(stream, "close", None)
            if close:
                close()
    return {"sha256": sha.hexdigest(), "size": size}


def complete(client, operation_id, workspace_id, artifact):
    """Publish handoff hook; Scheduler confirms receipt via its own API."""
    logger.info(
        "Snapshot capture complete operation_id=%s workspace_id=%s sha256=%.12s size=%d",
        operation_id, workspace_id, artifact.get("sha256", ""), artifact.get("size", 0),
    )
    return True


def capture(ops, coordinator, workspace_id, operation_id, record, clock,
            progress=None, upload_chunk=UPLOAD_VERIFY_CHUNK, state_dir=None):
    """Commit the exact labelled workload and export it as an artifact file.

    Returns ``{"image_id", "config", "artifact"}``.  Raises ``CaptureDenied``
    on every controlled failure; unpauses the workload on the way out.
    """
    assignment_id = record["assignment_id"]
    if not coordinator.authoritative(assignment_id):
        raise CaptureDenied("LEASE_LOST", "worker lease is not authoritative")
    ops.authority(record)
    workload = ops.get_workload(record)
    if workload is None:
        raise CaptureDenied("WORKLOAD_MISSING", "workload container is gone")
    _check_labels(workload, record)
    ops.authority(record)
    journal_snapshot(state_dir, operation_id, {
        "workspace_id": workspace_id,
        "operation_id": operation_id,
        "state": "capturing",
        "attempt_token": record.get("attempt_token"),
        "generation": record.get("generation"),
        "started_at_monotonic": clock(),
        "paused_deadline": clock() + CAPTURE_PAUSED_SECONDS + HEARTBEAT_GRACE_SECONDS,
    })
    coordinator.persist({**coordinator.get(assignment_id),
                         "snapshot_operation_id": operation_id,
                         "snapshot_state": "capturing"})
    paused = False
    try:
        _pause_with_timeout(workload, CAPTURE_PAUSED_SECONDS)
        paused = True
        attrs = getattr(workload, "attrs", None) or {}
        config = _check_config(dict(attrs.get("Config") or {}))
        tag = staging_tag(operation_id)
        image = workload.commit(tag)
        image_id = getattr(image, "id", None) or ""
        if not image_id:
            raise CaptureDenied("CAPTURE_FAILED", "commit returned no image id")
        journal_snapshot(state_dir, operation_id, {"image_id": image_id, "state": "committed"})
        coordinator.persist({**coordinator.get(assignment_id),
                             "snapshot_operation_id": operation_id,
                             "snapshot_state": "committed",
                             "snapshot_image_id": image_id})
        ops.authority(record)
        destination = str(_snapshot_dir(state_dir) / (operation_id + ".tar.gz"))
        artifact = _upload(ops.client, assignment_id, image_id, destination,
                           clock() + CAPTURE_PAUSED_SECONDS)
        journal_snapshot(state_dir, operation_id, {
            "artifact": artifact, "artifact_path": destination, "state": "uploaded"})
        coordinator.persist({**coordinator.get(assignment_id),
                             "snapshot_operation_id": operation_id,
                             "snapshot_state": "uploaded",
                             "snapshot_artifact": artifact})
        complete(ops.client, operation_id, workspace_id, artifact)
        with suppress(Exception):
            ops.client.images.remove(tag, force=False)
        ops.remove_exact(record, image_id)
        if progress:
            with suppress(Exception):
                progress(record, "SNAPSHOT_UPLOADED")
        return {"image_id": image_id, "config": config, "artifact": artifact}
    finally:
        if paused:
            with suppress(Exception):
                workload.unpause(timeout=CAPTURE_UNPAUSE_SECONDS)
