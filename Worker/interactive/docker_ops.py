"""Exact-ID Docker operations with cancellation fences around every mutation."""

import base64
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import docker
from docker.types import DeviceRequest, LogConfig
from scheduler_protocol import protected_file

LABEL = "dml.assignment"
TAILSCALE = "tailscale/tailscale:v1.102.3@sha256:8c42c4574ab066384fcb72f69e086a2ff1dd3652eb6f56856cee34bcf0d2f680"

import logging

logger = logging.getLogger("docker_ops")


def cleanup_on_failure() -> bool:
    """True when failed interactive containers should be removed.

    Opt-in via ``INTERACTIVE_CLEANUP_ON_FAILURE=1`` (also accepts
    ``true``/``yes``). When unset/``0``/``false`` the worker keeps failed
    interactive containers (workload/sidecar/access) stopped but present
    so operators can ``docker logs``/``docker inspect`` them for debugging.
    Successful assignments are always cleaned up regardless of this flag.
    """
    return os.getenv("INTERACTIVE_CLEANUP_ON_FAILURE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


class RuntimeFailure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


# Opt-in workload egress (plan.md Phase 1).  The Worker keeps a hard local
# gate: the Scheduler hint in launch_spec.allow_internet is advisory only and
# a browser value can never enable it.  Default stays offline (`none`).
def workload_internet_enabled() -> bool:
    value = os.getenv("INTERACTIVE_ALLOW_INTERNET", "0").strip().lower()
    return value in ("1", "true", "yes")


# Operator opt-in for the shared-kernel sudo profile (plan.md §3/§6).
# Default off; enforced independently of the Scheduler flag and never taken
# from a browser-supplied value. The existing strict path keeps its controls
# when this is off.
def developer_mode_enabled() -> bool:
    value = os.getenv("INTERACTIVE_ALLOW_DEVELOPER_MODE", "0").strip().lower()
    return value in ("1", "true", "yes")


DEVELOPER_PROFILE_LABEL = "io.dml.developer-profile"
DEVELOPER_PROFILE_VERSION = "v1"
DEVELOPER_USER = "10001:10001"
DEVELOPER_WORKDIR = "/workspace"
SSH_PROFILE_LABEL = "io.dml.vscode-ssh-profile"
SSH_PROFILE_VERSION = "v1"


def ssh_spec_enabled(spec) -> bool:
    """Server-owned SSH flag from the immutable launch spec."""
    try:
        return bool(spec.get("ssh_capable"))
    except Exception:
        return False


def ssh_allowed_locally() -> bool:
    return os.getenv("INTERACTIVE_ALLOW_SSH", "").strip().lower() in ("1", "true", "yes")


def ssh_image_capable(config) -> bool:
    try:
        labels = config.get("Labels") or {}
        return labels.get(SSH_PROFILE_LABEL) == SSH_PROFILE_VERSION
    except Exception:
        return False


def workload_developer_mode(spec) -> bool:
    """Server-owned developer flag from the immutable launch spec."""
    try:
        return bool(spec.get("developer_mode"))
    except Exception:
        return False


def workload_network_mode(spec) -> str | None:
    """None lets docker-py omit the key so the daemon default bridge applies."""
    allow = bool(spec.get("allow_internet")) and workload_internet_enabled()
    return None if allow else "none"


def canonical_registry_reference(value):
    """Normalize Docker Hub's optional ``docker.io/`` registry prefix.

    Docker Hub accepts both ``owner/image`` and ``docker.io/owner/image``.
    Image builders commonly return the former in a digest reference, while a
    Worker allowlist/credential describes the latter.  Canonicalizing before
    authorization keeps those equivalent spellings from becoming a false
    UNSUPPORTED_IMAGE/PULL_FAILED result.  Other registries retain their exact
    host name.
    """
    repository = value.split("@", 1)[0]
    first = repository.split("/", 1)[0]
    if "." in first or ":" in first or first == "localhost":
        return value
    return "docker.io/" + value


def labels(record, worker_id, component):
    p = record["payload"]
    common = {
        "dml.component": component,
        "dml.worker": worker_id,
        LABEL: record["assignment_id"],
    }
    if record["kind"] != "interactive_access":
        return {**common, "dml.job": p["id"]}
    return {
        **common,
        "dml.runtime": p["runtime_id"],
        "dml.workspace": p["workspace_id"],
        "dml.revision": p["revision_id"],
        "dml.generation": str(p["generation"]),
        "dml.owner": p["owner_id"],
    }


class DockerOps:
    def __init__(self, coordinator, worker_id, client=None):
        self.coordinator, self.worker_id = coordinator, worker_id
        self.client = client or docker.from_env(timeout=10)
        self.pull_lock = __import__("threading").Lock()

    def authority(self, record):
        if not self.coordinator.authoritative(record["assignment_id"]):
            raise RuntimeFailure("LEASE_LOST")

    def preflight(self):
        """An actual disposable size-limited container tests daemon quota support."""
        image = os.getenv("INTERACTIVE_PREFLIGHT_IMAGE", "")
        if not re.search(r"@sha256:[0-9a-f]{64}$", image):
            return False
        if not hasattr(os, "pidfd_open") or os.geteuid() != 0:
            return False
        try:
            info = self.client.info()
            has_nvidia = "nvidia" in info.get("Runtimes", {})
            driver = info.get("Driver", "")
            # Legacy graph driver (overlay2) OR containerd v1 with overlayfs
            # snapshotter — both provide the quota support the test exercises.
            containerd_overlay = any(
                entry[0] == "driver-type" and entry[1] == "io.containerd.snapshotter.v1"
                for entry in info.get("DriverStatus", [])
            )
            if not has_nvidia or not (
                driver == "overlay2" or containerd_overlay
            ):
                return False
            # Operators pre-pull this tiny shell fixture. No production workload
            # credentials/mounts or broad Docker cleanup are used by preflight.
            c = self.client.containers.create(
                image,
                command=["-c", "exit 0"],
                entrypoint="/bin/sh",
                network_mode="none",
                storage_opt={"size": "1G"},
                labels={
                    "dml.component": "quota-preflight",
                    "dml.worker": self.worker_id,
                },
            )
            try:
                c.start()
                return c.wait(timeout=10)["StatusCode"] == 0
            finally:
                c.remove(force=True)
        except Exception:
            return False

    def pull(self, record):
        ref = record["payload"]["image_digest_ref"]
        if not re.fullmatch(r"[A-Za-z0-9./:_-]+@sha256:[0-9a-f]{64}", ref):
            raise RuntimeFailure("UNSUPPORTED_IMAGE")
        canonical_ref = canonical_registry_reference(ref)
        allowed = [
            canonical_registry_reference(x.strip().rstrip("/"))
            for x in os.getenv("INTERACTIVE_REGISTRY_PREFIXES", "").split(",")
            if x.strip()
        ]
        if not any(canonical_ref.startswith(prefix + "/") for prefix in allowed):
            raise RuntimeFailure("UNSUPPORTED_IMAGE")
        self.authority(record)
        with self.pull_lock:
            # Tmpfs Docker config avoids credentials in arguments/env or journal.
            root = Path("/run/dml-interactive-pulls")
            root.mkdir(mode=0o700, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=root) as directory:
                credentials = os.getenv("INTERACTIVE_REGISTRY_CREDENTIAL_FILE")
                if credentials:
                    value = json.loads(protected_file(credentials))
                    if (
                        not isinstance(value, dict)
                        or set(value) != {"server", "username", "password"}
                        or not all(isinstance(v, str) and v for v in value.values())
                        or not re.fullmatch(r"[A-Za-z0-9.:-]+", value["server"])
                        or ":" in value["username"]
                    ):
                        raise RuntimeFailure("PULL_FAILED")
                    server = value["server"]
                    if not canonical_ref.startswith(server + "/"):
                        raise RuntimeFailure("PULL_FAILED")
                    auth = base64.b64encode(
                        (value["username"] + ":" + value["password"]).encode()
                    ).decode()
                    config = Path(directory) / "config.json"
                    config.write_text(json.dumps({"auths": {server: {"auth": auth}}}))
                    config.chmod(0o600)
                try:
                    proc = subprocess.Popen(
                        [
                            "docker",
                            "--config",
                            directory,
                            "pull",
                            "--platform",
                            record["payload"]["launch_spec"]["platform"],
                            canonical_ref,
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    deadline = __import__("time").monotonic() + int(
                        os.getenv("INTERACTIVE_PULL_TIMEOUT_SECONDS", "1800")
                    )
                    while proc.poll() is None:
                        if (
                            not self.coordinator.authoritative(record["assignment_id"])
                            or __import__("time").monotonic() >= deadline
                        ):
                            proc.kill()
                            proc.wait(timeout=5)
                            raise RuntimeFailure("PULL_FAILED")
                        __import__("time").sleep(0.2)
                    if proc.returncode:
                        raise RuntimeFailure("PULL_FAILED")
                except OSError:
                    raise RuntimeFailure("PULL_FAILED") from None
        self.authority(record)
        image = self.client.images.get(canonical_ref)
        attrs = image.attrs
        spec = record["payload"]["launch_spec"]
        config = attrs.get("Config", {})
        if (
            canonical_ref
            not in {
                canonical_registry_reference(digest)
                for digest in attrs.get("RepoDigests", [])
            }
            or attrs.get("Os") + "/" + attrs.get("Architecture") != spec["platform"]
            or config.get("Volumes")
        ):
            raise RuntimeFailure("UNSUPPORTED_IMAGE")
        user, workdir = (
            config.get("User") or "",
            config.get("WorkingDir") or "/workspace",
        )
        if (
            not spec["allow_root"]
            and (not user or user.split(":")[0] in ("0", "root"))
            or len(user) > 128
            or not workdir.startswith("/")
            or len(workdir) > 1024
        ):
            raise RuntimeFailure("UNSUPPORTED_IMAGE")
        if workload_developer_mode(spec):
            # Authoritative local gates: a developer runtime must never start
            # as a silent strict/offline runtime. Fail before any workload.
            if not developer_mode_enabled():
                raise RuntimeFailure("START_FAILED")
            if bool(spec.get("allow_internet")) and not workload_internet_enabled():
                raise RuntimeFailure("START_FAILED")
            labels = config.get("Labels") or {}
            if labels.get(DEVELOPER_PROFILE_LABEL) != DEVELOPER_PROFILE_VERSION:
                raise RuntimeFailure("UNSUPPORTED_IMAGE")
            if user != DEVELOPER_USER or workdir != DEVELOPER_WORKDIR:
                raise RuntimeFailure("UNSUPPORTED_IMAGE")
        return image.id, user, workdir

    def image_labels(self, image_id):
        try:
            image = self.client.images.get(image_id)
            return ((image.attrs.get("Config") or {}).get("Labels") or {})
        except Exception:
            return {}

    def create(self, record, component, image, **kwargs):
        self.authority(record)
        # Pull trusted service images by pinned digest explicitly; container
        # create must never resolve an absent mutable tag as a fallback.
        if component in ("sidecar", "access"):
            self.client.images.pull(image)
            self.authority(record)
        c = self.client.containers.create(
            image,
            name="dml-" + record["assignment_id"] + "-" + component,
            labels=labels(record, self.worker_id, component),
            restart_policy={"Name": "no"},
            log_config=LogConfig(
                type="json-file", config={"max-size": "10m", "max-file": "2"}
            ),
            **kwargs
        )
        # Persist exact ID before start. If Stop raced the create, journal/labels
        # identify the late object and cleanup can remove it immediately.
        with self.coordinator.lock:
            current = self.coordinator.get(record["assignment_id"])
            current["containers"][component] = c.id
            self.coordinator.persist(current)
        try:
            self.authority(record)
            c.start()
            self.authority(record)
        except Exception:
            # A start/lease failure leaves a failed container behind. Keep it
            # for debugging unless INTERACTIVE_CLEANUP_ON_FAILURE is set; the
            # Manager's finally-block cleanup honors the same flag.
            if cleanup_on_failure():
                self.remove_exact(record, c.id)
            else:
                logger.warning(
                    "Keeping failed %s container %s for debugging "
                    "(INTERACTIVE_CLEANUP_ON_FAILURE not set)",
                    component,
                    c.id,
                )
            raise
        return c

    def workload(self, record, image_id, user, workdir):
        from hardware import execution_inventory

        inv = execution_inventory(
            self.coordinator, interactive_ready=True, quota_supported=True
        )
        p, spec = record["payload"], record["payload"]["launch_spec"]
        if (
            not inv["complete"]
            or any(g["busy"] or g["processes"] for g in inv["gpus"])
            or p["gpu_uuid"] not in [g["uuid"] for g in inv["gpus"]]
        ):
            raise RuntimeFailure("GPU_BUSY")
        if (
            inv["free_disk_gb"] < spec["disk_gb"] + spec["pull_headroom_gb"]
            or inv["free_ram_gb"] < spec["memory_gb"] + 1
        ):
            raise RuntimeFailure("DISK_FULL")
        network = workload_network_mode(spec)
        developer = workload_developer_mode(spec)
        if developer:
            # Re-check locally: Scheduler placement is advisory, the Worker's
            # own gates remain authoritative. Never silently downgrade.
            if not developer_mode_enabled():
                raise RuntimeFailure("START_FAILED")
            if bool(spec.get("allow_internet")) and not workload_internet_enabled():
                raise RuntimeFailure("START_FAILED")
        logger.info(
            "Launching workload assignment_id=%s network=%s gpu_uuid=%s developer=%s",
            record["assignment_id"],
            "bridge" if network is None else network,
            p["gpu_uuid"],
            developer,
        )
        base_kwargs = dict(
            entrypoint="/bin/sh",
            command=[
                "-c",
                'trap "exit 0" TERM INT; while :; do sleep 3600 & wait $!; done',
            ],
            user=user,
            working_dir=workdir,
            healthcheck={"test": ["NONE"]},
            init=True,
            network_mode=network,
            pids_limit=spec["pids"],
            nano_cpus=int(spec["cpu"] * 1e9),
            mem_limit=int(spec["memory_gb"] * 1024**3),
            memswap_limit=int(spec["memory_gb"] * 1024**3),
            storage_opt={"size": str(spec["disk_gb"]) + "G"},
            device_requests=[
                DeviceRequest(device_ids=[p["gpu_uuid"]], capabilities=[["gpu"]])
            ],
        )
        if developer:
            # Operator-enabled developer mode: Docker's normal restricted
            # capability set with setuid permitted so `sudo` works inside the
            # workload only. Never privileged, host mounts/net, extra GPUs.
            return self.create(record, "workload", image_id, **base_kwargs)
        return self.create(
            record,
            "workload",
            image_id,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            **base_kwargs
        )

    def unit(self, record, runtime_dir, endpoint_dir):
        image = os.environ["INTERACTIVE_ACCESS_IMAGE"]
        if not re.search(r"@sha256:[0-9a-f]{64}$", image):
            raise RuntimeFailure("START_FAILED")
        endpoint_mounts = {str(endpoint_dir): {"bind": "/state", "mode": "rw"}}
        endpoint_env = {"TS_NO_LOGS_NO_SUPPORT": "true"}
        ca = os.getenv("INTERACTIVE_HEADSCALE_CA_FILE")
        if ca:
            endpoint_mounts[ca] = {"bind": "/ca/headscale.pem", "mode": "ro"}
            endpoint_env["SSL_CERT_FILE"] = "/ca/headscale.pem"
        sidecar = self.create(
            record,
            "sidecar",
            TAILSCALE,
            entrypoint="tailscaled",
            command=[
                "--tun=userspace-networking",
                "--state=/state/tailscaled.state",
                "--socket=/state/tailscaled.sock",
            ],
            environment=endpoint_env,
            volumes=endpoint_mounts,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            mem_limit="512m",
            nano_cpus=1000000000,
            pids_limit=128,
        )
        access = self.create(
            record,
            "access",
            image,
            network_mode="container:" + sidecar.id,
            user="10001:10001",
            environment={
                "ACCESS_RUNTIME_ID": record["assignment_id"],
                "ACCESS_BROKER_SOCKET": str(runtime_dir / "broker.sock"),
                "ACCESS_BROKER_TOKEN_FILE": str(runtime_dir / "broker.token"),
                "ACCESS_SSH_CAPACITY": os.getenv("INTERACTIVE_SSH_CAPACITY", "8"),
            },
            volumes={str(runtime_dir): {"bind": str(runtime_dir), "mode": "ro"}},
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            mem_limit="256m",
            nano_cpus=500000000,
            pids_limit=64,
        )
        return sidecar, access

    def get_workload(self, record):
        """Return the exact labelled workload container, or None if absent.

        Never resolves sidecar/access containers: the journaled workload id is
        re-fetched and its labels re-verified (component + assignment), so a
        recycled container id or stale journal entry fails closed instead of
        capturing the wrong container.  Used by the snapshot capture path.
        """
        container_id = (record.get("containers") or {}).get("workload")
        if not container_id:
            return None
        try:
            container = self.client.containers.get(container_id)
        except docker.errors.NotFound:
            return None
        expected = labels(record, self.worker_id, "workload")
        actual = container.labels or {}
        if any(actual.get(key) != value for key, value in expected.items()):
            raise RuntimeFailure("LOCAL_CONFLICT")
        return container

    def remove_exact(self, record, container_id):
        try:
            c = self.client.containers.get(container_id)
        except docker.errors.NotFound:
            return
        expected = labels(record, self.worker_id, c.labels.get("dml.component"))
        if any(c.labels.get(key) != value for key, value in expected.items()):
            raise RuntimeFailure("LOCAL_CONFLICT")
        c.remove(force=True, v=False)
        try:
            self.client.containers.get(container_id)
        except docker.errors.NotFound:
            return
        raise RuntimeFailure("START_FAILED")

    def cleanup(self, record):
        # Keep failed interactive runtimes for debugging unless the operator
        # opted into removal via INTERACTIVE_CLEANUP_ON_FAILURE=1. Successful
        # assignments (no runtime_failure_code) are always cleaned up, as are
        # expected TIME_UP expiries: the 10-minute cap is not a debuggable
        # failure and the containers must be killed on time up.
        if (
            record.get("runtime_failure_code")
            not in (None, "", "TIME_UP")
            and not cleanup_on_failure()
        ):
            logger.warning(
                "Keeping failed interactive containers for debugging "
                "assignment_id=%s code=%s "
                "(set INTERACTIVE_CLEANUP_ON_FAILURE=1 to remove automatically)",
                record.get("assignment_id"),
                record.get("runtime_failure_code"),
            )
            return
        # Exact assignment selectors recover create-before-journal crash windows.
        objects = self.client.containers.list(
            all=True,
            filters={
                "label": [
                    LABEL + "=" + record["assignment_id"],
                    "dml.worker=" + self.worker_id,
                ]
            },
        )
        for component in (
            "access",
            "sidecar",
            "workload",
            "batch",
            "batch-seed",
            "batch-cleanup",
        ):
            for c in objects:
                if c.labels.get("dml.component") == component:
                    self.remove_exact(record, c.id)
        if self.client.containers.list(
            all=True,
            filters={
                "label": [
                    LABEL + "=" + record["assignment_id"],
                    "dml.worker=" + self.worker_id,
                ]
            },
        ):
            raise RuntimeFailure("LOCAL_CONFLICT")
