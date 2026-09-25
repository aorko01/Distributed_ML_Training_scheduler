"""Durable workspace capture (plan.md Phase 2).

One capture per save operation: gate, exact-label ``docker commit --pause``,
streamed ``docker save | gzip`` upload, staging-tag cleanup.  Every Docker side
effect is fenced by the Worker lease (``DockerOps.authority``) and journaled
before it runs.  The module never talks to a registry and never prunes.
"""

from contextlib import suppress
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

CAPTURE_PAUSED_SECONDS = 300
UPLOAD_VERIFY_CHUNK = 1 << 20
HEARTBEAT_GRACE_SECONDS = 20
MAX_ARTIFACT = 8 * 1024 * 1024 * 1024
MAX_EXPORT_SECONDS = 1800


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
    payload = record.get("payload") or {}
    if (
        labels.get("dml.component") != "workload"
        or labels.get("dml.assignment") != record["assignment_id"]
        or labels.get("dml.worker") != record.get("worker_id", labels.get("dml.worker"))
        or labels.get("dml.runtime") != payload.get("runtime_id")
        or labels.get("dml.workspace") != payload.get("workspace_id")
        or labels.get("dml.revision") != payload.get("revision_id")
        or labels.get("dml.generation") != str(payload.get("generation"))
    ):
        raise CaptureDenied("CAPTURE_FORBIDDEN", "workload identity mismatch")


def _check_config(config, mounts=(), ssh_required=False, host_config=None):
    if config.get("Volumes"):
        raise CaptureDenied("VOLUME_IMAGE", "volume-backed image is not portable")
    if config.get("User") != "10001:10001" or config.get("WorkingDir") != "/workspace":
        raise CaptureDenied("IMAGE_CONFIG", "workspace image identity changed")
    saved_paths = ("/workspace", "/opt/dml-venv", "/home/dml", "/usr", "/etc", "/var")
    ssh_mount = "/run/dml-vscode-ssh" in ((host_config or {}).get("Tmpfs") or {})
    for item in mounts:
        dest = str(item.get("Destination") or "").rstrip("/")
        if any(dest == path or path.startswith(dest + "/") or dest.startswith(path + "/") for path in saved_paths):
            raise CaptureDenied("VOLUME_IMAGE", "saved filesystem path is mounted")
        if dest == "/run/dml-vscode-ssh" and item.get("Type") == "tmpfs":
            ssh_mount = True
    if ssh_required and not ssh_mount:
        raise CaptureDenied("SSH_STATE_UNSAFE", "runtime SSH state is not ephemeral")
    return config


def _upload(client, assignment_id, image_id, destination, deadline, authority=lambda: True):
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
                    if time.monotonic() > deadline or not authority():
                        raise CaptureDenied("CAPTURE_TIMEOUT", "export deadline or lease exceeded")
                    if not chunk:
                        continue
                    out.write(chunk)
                    if raw.tell() > MAX_ARTIFACT:
                        raise CaptureDenied("ARTIFACT_TOO_LARGE")
        with open(destination, "rb") as check:
            while True:
                piece = check.read(UPLOAD_VERIFY_CHUNK)
                if not piece:
                    break
                sha.update(piece)
                size += len(piece)
                if size > MAX_ARTIFACT:
                    raise CaptureDenied("ARTIFACT_TOO_LARGE")
                if not authority():
                    raise CaptureDenied("LEASE_LOST")
    finally:
        with suppress(Exception):
            close = getattr(stream, "close", None)
            if close:
                close()
    return {"sha256": sha.hexdigest(), "size": size}


def capture(ops, coordinator, workspace_id, operation_id, record, clock,
            progress=None, upload_chunk=UPLOAD_VERIFY_CHUNK, state_dir=None,
            on_committed=None, authority=None):
    """Commit the exact labelled workload and export it as an artifact file.

    Returns ``{"image_id", "config", "artifact"}``.  Raises ``CaptureDenied``
    on every controlled failure; unpauses the workload on the way out.
    """
    assignment_id = record["assignment_id"]
    authority = authority or (lambda: coordinator.authoritative(assignment_id))
    if not authority():
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
    tag = staging_tag(operation_id)
    try:
        attrs = getattr(workload, "attrs", None) or {}
        spec = (record.get("payload") or {}).get("launch_spec") or {}
        config = _check_config(dict(attrs.get("Config") or {}), attrs.get("Mounts") or (),
                               bool(spec.get("ssh_capable")), attrs.get("HostConfig") or {})
        # Docker pauses only for the commit transaction. Export and transport
        # run after the workload resumes, preserving live VS Code sessions.
        repository, tag_name = tag.split(":", 1)
        try:
            image = ops.commit_image(record, repository, tag_name)
        except Exception:
            raise CaptureDenied("CAPTURE_FAILED", "Docker commit failed") from None
        image_id = getattr(image, "id", None) or ""
        if not image_id:
            raise CaptureDenied("CAPTURE_FAILED", "commit returned no image id")
        journal_snapshot(state_dir, operation_id, {"image_id": image_id, "state": "committed"})
        coordinator.persist({**coordinator.get(assignment_id),
                             "snapshot_operation_id": operation_id,
                             "snapshot_state": "committed",
                             "snapshot_image_id": image_id})
        if on_committed:
            on_committed()
        if not authority():
            raise CaptureDenied("LEASE_LOST")
        ops.authority(record)
        destination = str(_snapshot_dir(state_dir) / (operation_id + ".tar.gz"))
        export_client = ops.snapshot_client() if hasattr(type(ops), "snapshot_client") else ops.client
        try:
            artifact = _upload(export_client, assignment_id, image_id, destination,
                               clock() + MAX_EXPORT_SECONDS, authority=authority)
        finally:
            if export_client is not ops.client:
                export_client.close()
        journal_snapshot(state_dir, operation_id, {
            "artifact": artifact, "artifact_path": destination, "state": "uploaded"})
        coordinator.persist({**coordinator.get(assignment_id),
                             "snapshot_operation_id": operation_id,
                             "snapshot_state": "uploaded",
                             "snapshot_artifact": artifact})
        if progress:
            with suppress(Exception):
                progress(record, "SNAPSHOT_UPLOADED")
        return {"image_id": image_id, "config": config, "artifact": artifact,
                "artifact_path": destination}
    finally:
        with suppress(Exception):
            workload.reload()
            if workload.status == "paused":
                workload.unpause()
