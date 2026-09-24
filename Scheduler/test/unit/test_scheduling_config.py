"""Interactive launch profile: operator opt-in egress stays off by default."""

import pytest
from app.services.scheduling.config import resource_profile


def profile(env_value, monkeypatch):
    if env_value is None:
        monkeypatch.delenv("INTERACTIVE_INTERNET_ENABLED", raising=False)
    else:
        monkeypatch.setenv("INTERACTIVE_INTERNET_ENABLED", env_value)
    return resource_profile()


@pytest.mark.parametrize(
    "env_value, expected",
    [
        (None, False),
        ("", False),
        ("0", False),
        ("junk", False),
        ("1", True),
    ],
)
def test_internet_flag_defaults_off_and_parses_exact_one(monkeypatch, env_value, expected):
    assert profile(env_value, monkeypatch)["allow_internet"] is expected


def test_profile_keeps_runtime_shape_and_valid_bool(monkeypatch):
    spec = profile("1", monkeypatch)
    assert spec["version"]
    assert type(spec["allow_internet"]) is bool
    for key in ("platform", "allow_root", "disk_gb", "minimum_vram_gb"):
        assert key in spec


def test_developer_mode_defaults_off_and_never_from_browser(monkeypatch):
    from app.services.scheduling import config as sched_config
    from pydantic import ValidationError
    from app.schemas.interactive_capacity_schema import ResourceRequirements

    monkeypatch.delenv("INTERACTIVE_DEVELOPER_MODE_ENABLED", raising=False)
    assert sched_config.resource_profile()["developer_mode"] is False
    monkeypatch.setenv("INTERACTIVE_DEVELOPER_MODE_ENABLED", "1")
    assert sched_config.resource_profile()["developer_mode"] is True
    monkeypatch.setenv("INTERACTIVE_DEVELOPER_MODE_ENABLED", "0")
    spec = sched_config.build_launch_spec({"gpu_model": None, "minimum_vram_gb": 4, "cpu_cores": 2, "memory_gb": 8, "disk_gb": 20})
    assert spec["developer_mode"] is False
    # Browser requirements object cannot set privilege/network policy.
    with pytest.raises(ValidationError):
        ResourceRequirements(minimum_vram_gb=4, cpu_cores=2, memory_gb=8, disk_gb=20, developer_mode=True)  # type: ignore[call-arg]
