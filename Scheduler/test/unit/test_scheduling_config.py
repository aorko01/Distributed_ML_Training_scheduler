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
