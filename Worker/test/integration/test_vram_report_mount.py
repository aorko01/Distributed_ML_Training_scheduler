"""Real-Docker regression for the VRAM estimator's bind-mounted report."""

import json
import os
import shutil
import subprocess

import pytest

from executor import JobExecutor, _docker_host_path


@pytest.mark.skipif(os.name != "posix" or os.geteuid() != 0, reason="requires root chown")
def test_uid_10001_can_write_private_report_bind_mount(tmp_path):
    """A 0700 report directory owned by the image user works through Docker."""
    if not shutil.which("docker"):
        pytest.skip("docker CLI unavailable")
    image = os.environ.get("VRAM_REPORT_TEST_IMAGE", "python:3.12-slim")
    inspect = subprocess.run(
        ["docker", "image", "inspect", image], capture_output=True, text=True,
    )
    if inspect.returncode:
        pytest.skip(f"local test image unavailable: {image}")

    report_dir = tmp_path / "report"
    report_dir.mkdir()
    JobExecutor._prepare_vram_report_dir(str(report_dir), 10001, 10001)
    result = subprocess.run(
        [
            "docker", "run", "--rm", "--network", "none",
            "--user", "10001:10001",
            "-v", f"{_docker_host_path(str(report_dir))}:/report",
            "--entrypoint", "python", image,
            "-c", "import json; json.dump({'ok': True}, open('/report/report.json', 'w'))",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads((report_dir / "report.json").read_text()) == {"ok": True}
