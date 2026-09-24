import os
import math
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    admission: bool = False
    interactive: bool = False
    lease_seconds: int = 45
    fresh_seconds: int = 15
    startup_seconds: int = 1920
    lifetime_seconds: int = 0
    workspace_editor: bool = False
    workspace_save: bool = False
    workspace_training_submission: bool = False

    @classmethod
    def from_env(cls):
        value = cls(
            os.getenv("WORKER_NEW_WORK_ENABLED", "0") == "1",
            os.getenv("INTERACTIVE_RUNTIME_ENABLED", "0") == "1",
            int(os.getenv("WORKER_ASSIGNMENT_LEASE_SECONDS", "45")),
            15,
            int(os.getenv("INTERACTIVE_STARTUP_SECONDS", "1920")),
            int(os.getenv("INTERACTIVE_LIFETIME_SECONDS", "0")),
            os.getenv("WORKSPACE_EDITOR_ENABLED", "0") == "1",
            os.getenv("WORKSPACE_SAVE_ENABLED", "0") == "1",
            os.getenv("WORKSPACE_TRAINING_SUBMISSION_ENABLED", "0") == "1",
        )
        if (
            value.lease_seconds < 20
            or value.startup_seconds < 10
            or value.lifetime_seconds < 0
        ):
            raise ValueError("Invalid execution deadlines")
        return value


def developer_mode_enabled() -> bool:
    return os.getenv("INTERACTIVE_DEVELOPER_MODE_ENABLED", "0").strip().lower() in ("1", "true", "yes")


def internet_enabled() -> bool:
    return os.getenv("INTERACTIVE_INTERNET_ENABLED", "0").strip().lower() in ("1", "true", "yes")


def _float_env(name, default):
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        raise ValueError(f"Invalid {name}") from None
    if not math.isfinite(value):
        raise ValueError(f"Invalid {name}")
    return value


def _int_env(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        raise ValueError(f"Invalid {name}") from None


def operator_defaults():
    """Operator-owned default resource profile (backward compatible)."""
    return {
        "version": os.getenv("INTERACTIVE_PROFILE_VERSION", "gpu-v1"),
        "platform": os.getenv("INTERACTIVE_PLATFORM", "linux/amd64"),
        "cpu": float(os.getenv("INTERACTIVE_CPU", "2")),
        "memory_gb": float(os.getenv("INTERACTIVE_RAM_GB", "8")),
        "disk_gb": int(os.getenv("INTERACTIVE_WRITABLE_GB", "20")),
        "pull_headroom_gb": int(os.getenv("INTERACTIVE_PULL_HEADROOM_GB", "40")),
        "pids": int(os.getenv("INTERACTIVE_PIDS", "256")),
        "minimum_vram_gb": float(os.getenv("INTERACTIVE_MIN_VRAM_GB", "4")),
        "gpu_models": [
            x.strip() for x in os.getenv("INTERACTIVE_GPU_MODELS", "").split(",") if x.strip()
        ],
        "allow_root": os.getenv("INTERACTIVE_ALLOW_ROOT", "0") == "1",
        "allow_internet": internet_enabled(),
        "developer_mode": developer_mode_enabled(),
    }


def operator_bounds():
    """Operator-configured user-selectable minimum/maximum limits."""
    defaults = operator_defaults()
    bounds = {
        "cpu_cores": {
            "min": _float_env("INTERACTIVE_MIN_CPU", "0.5"),
            "max": _float_env("INTERACTIVE_MAX_CPU", "64"),
            "default": float(defaults["cpu"]),
        },
        "memory_gb": {
            "min": _float_env("INTERACTIVE_MIN_RAM_GB", "0.5"),
            "max": _float_env("INTERACTIVE_MAX_RAM_GB", "512"),
            "default": float(defaults["memory_gb"]),
        },
        "disk_gb": {
            "min": _int_env("INTERACTIVE_MIN_WRITABLE_GB", "1"),
            "max": _int_env("INTERACTIVE_MAX_WRITABLE_GB", "2000"),
            "default": int(defaults["disk_gb"]),
        },
        "minimum_vram_gb": {
            "min": _float_env("INTERACTIVE_MIN_VRAM_GB", "0.5")
            if "INTERACTIVE_MIN_VRAM_GB" in os.environ
            else 0.5,
            "max": _float_env("INTERACTIVE_MAX_VRAM_GB", "192"),
            "default": float(defaults["minimum_vram_gb"]),
        },
        "gpu_models_allowlist": list(defaults["gpu_models"]),
    }
    for key in ("cpu_cores", "memory_gb", "minimum_vram_gb"):
        lo, hi = bounds[key]["min"], bounds[key]["max"]
        if not (math.isfinite(lo) and math.isfinite(hi) and 0 < lo <= hi):
            raise ValueError("Invalid interactive resource bounds")
        if not (lo <= bounds[key]["default"] <= hi):
            raise ValueError("Invalid interactive resource bounds")
    dmin, dmax = bounds["disk_gb"]["min"], bounds["disk_gb"]["max"]
    if not (0 < dmin <= dmax) or not (dmin <= bounds["disk_gb"]["default"] <= dmax):
        raise ValueError("Invalid interactive resource bounds")
    return bounds


def _validate_profile_shape(profile):
    if (
        profile["platform"] not in ("linux/amd64", "linux/arm64")
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", profile["version"])
        or not all(
            math.isfinite(profile[key]) and profile[key] > 0
            for key in ("cpu", "memory_gb", "minimum_vram_gb")
        )
        or not isinstance(profile["allow_internet"], bool)
        or not isinstance(profile["allow_root"], bool)
        or not isinstance(profile.get("developer_mode", False), bool)
        or not 1 <= profile["disk_gb"] <= 1000000
        or profile["pull_headroom_gb"] < 1
        or not 16 <= profile["pids"] <= 4096
    ):
        raise ValueError("Invalid interactive resource profile")


def build_launch_spec(requirements=None):
    """Build a safe immutable launch_spec from operator policy + user minimums.

    requirements is a canonical dict (or None for legacy defaults). Only
    numeric minimums and the optional GPU model are user-controlled; platform,
    PID limit, root policy, internet policy, pull headroom, and profile
    version remain server-owned.
    """
    defaults = operator_defaults()
    _validate_profile_shape(defaults)
    bounds = operator_bounds()
    spec = dict(defaults)
    if requirements is None:
        return spec
    req = dict(requirements)
    cpu = float(req["cpu_cores"])
    mem = float(req["memory_gb"])
    disk = int(req["disk_gb"])
    vram = float(req["minimum_vram_gb"])
    gpu_model = req.get("gpu_model")
    if gpu_model is not None:
        gpu_model = str(gpu_model).strip() or None
    if not (bounds["cpu_cores"]["min"] <= cpu <= bounds["cpu_cores"]["max"]):
        raise ValueError("CPU outside operator bounds")
    if not (bounds["memory_gb"]["min"] <= mem <= bounds["memory_gb"]["max"]):
        raise ValueError("RAM outside operator bounds")
    if not (bounds["disk_gb"]["min"] <= disk <= bounds["disk_gb"]["max"]):
        raise ValueError("Disk outside operator bounds")
    if not (bounds["minimum_vram_gb"]["min"] <= vram <= bounds["minimum_vram_gb"]["max"]):
        raise ValueError("VRAM outside operator bounds")
    allowlist = bounds["gpu_models_allowlist"]
    if gpu_model is not None:
        if len(gpu_model) > 128 or not gpu_model.strip():
            raise ValueError("Invalid GPU model")
        if allowlist and gpu_model not in allowlist:
            raise ValueError("GPU model not allowed by operator")
        spec["gpu_models"] = [gpu_model]
    else:
        spec["gpu_models"] = list(allowlist)
    spec["cpu"] = cpu
    spec["memory_gb"] = mem
    spec["disk_gb"] = disk
    spec["minimum_vram_gb"] = vram
    _validate_profile_shape(spec)
    return spec


def resource_profile(requirements=None):
    # Backward compatible: no requirements -> operator defaults.
    # Owners cannot provide launch arguments beyond validated minimums.
    if requirements is None:
        profile = operator_defaults()
        # Opt-in workload egress (plan.md Phase 1).  Scheduler-side hint only;
        # each Worker re-checks its own INTERACTIVE_ALLOW_INTERNET gate and
        # fails closed to network "none" when its local flag is off.
        _validate_profile_shape(profile)
        return profile
    if hasattr(requirements, "canonical"):
        requirements = requirements.canonical()
    return build_launch_spec(requirements)
