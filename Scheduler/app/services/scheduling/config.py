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

    @classmethod
    def from_env(cls):
        value = cls(
            os.getenv("WORKER_NEW_WORK_ENABLED", "0") == "1",
            os.getenv("INTERACTIVE_RUNTIME_ENABLED", "0") == "1",
            int(os.getenv("WORKER_ASSIGNMENT_LEASE_SECONDS", "45")),
            15,
            int(os.getenv("INTERACTIVE_STARTUP_SECONDS", "1920")),
            int(os.getenv("INTERACTIVE_LIFETIME_SECONDS", "0")),
        )
        if (
            value.lease_seconds < 20
            or value.startup_seconds < 10
            or value.lifetime_seconds < 0
        ):
            raise ValueError("Invalid execution deadlines")
        return value


def resource_profile():
    # One fixed operator profile; owners cannot provide launch arguments.
    profile = {
        "version": os.getenv("INTERACTIVE_PROFILE_VERSION", "gpu-v1"),
        "platform": os.getenv("INTERACTIVE_PLATFORM", "linux/amd64"),
        "cpu": float(os.getenv("INTERACTIVE_CPU", "2")),
        "memory_gb": float(os.getenv("INTERACTIVE_RAM_GB", "8")),
        "disk_gb": int(os.getenv("INTERACTIVE_WRITABLE_GB", "20")),
        "pull_headroom_gb": int(os.getenv("INTERACTIVE_PULL_HEADROOM_GB", "40")),
        "pids": int(os.getenv("INTERACTIVE_PIDS", "256")),
        "minimum_vram_gb": float(os.getenv("INTERACTIVE_MIN_VRAM_GB", "4")),
        "gpu_models": [
            x for x in os.getenv("INTERACTIVE_GPU_MODELS", "").split(",") if x
        ],
        "allow_root": os.getenv("INTERACTIVE_ALLOW_ROOT", "0") == "1",
    }

    if (
        profile["platform"] not in ("linux/amd64", "linux/arm64")
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", profile["version"])
        or not all(
            math.isfinite(profile[key]) and profile[key] > 0
            for key in ("cpu", "memory_gb", "minimum_vram_gb")
        )
        or not 1 <= profile["disk_gb"] <= 1000000
        or profile["pull_headroom_gb"] < 1
        or not 16 <= profile["pids"] <= 4096
    ):
        raise ValueError("Invalid interactive resource profile")
    return profile
