"""Check real production WSS using an isolated, temporary managed endpoint.

Requires a prebuilt E2E fixture image and operator access to Docker/controller.
Never mounts Docker into the driver or modifies existing node records directly.
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-image", required=True)
    parser.add_argument("--public-url", required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--controller-file", default="/etc/distributed-ml/interactive-secrets/controller")
    parser.add_argument("--management", default="http://dml-interactive-management-1:8020/internal/v1/")
    args = parser.parse_args()
    project = "interactive-host-smoke-" + uuid.uuid4().hex[:10]
    driver = str(Path(__file__).with_name("host_smoke_driver.py").resolve())
    manifest = {"services": {
        "endpoint": {"image": "tailscale/tailscale:v1.102.3@sha256:8c42c4574ab066384fcb72f69e086a2ff1dd3652eb6f56856cee34bcf0d2f680",
            "entrypoint": ["tailscaled", "--tun=userspace-networking", "--socks5-server=127.0.0.1:1055",
                "--state=/state/tailscaled.state", "--socket=/var/run/tailscale/tailscaled.sock"],
            "volumes": ["state:/state", "socket:/var/run/tailscale"], "networks": ["control"]},
        "driver": {"image": args.fixture_image, "network_mode": "service:endpoint",
            "entrypoint": ["python", "/smoke/driver.py"],
            "environment": {"PYTHONPATH": "/fixture", "SMOKE_RESOURCE": project, "SMOKE_MANAGEMENT": args.management,
                "SMOKE_PUBLIC_URL": args.public_url.rstrip("/"), "SMOKE_ORIGIN": args.origin},
            "volumes": ["socket:/var/run/tailscale", driver + ":/smoke/driver.py:ro",
                str(Path(args.controller_file).resolve()) + ":/secrets/controller:ro"]}},
        "volumes": {"state": {}, "socket": {}},
        "networks": {"control": {"external": True, "name": "dml-control"}}}
    with tempfile.TemporaryDirectory(prefix=project) as directory:
        filename = Path(directory) / "compose.json"
        filename.write_text(json.dumps(manifest))
        command = ["docker", "compose", "--project-name", project, "--file", str(filename)]
        try:
            subprocess.run(command + ["up", "--detach", "endpoint"], check=True, timeout=60)
            subprocess.run(command + ["run", "--rm", "driver"], check=True, timeout=240)
        finally:
            subprocess.run(command + ["down", "--volumes", "--remove-orphans"], check=True, timeout=60)
            remaining = subprocess.check_output(["docker", "ps", "-aq", "--filter",
                "label=com.docker.compose.project=" + project], text=True, timeout=10)
            if remaining.strip():
                raise RuntimeError("temporary smoke containers remain")


if __name__ == "__main__":
    main()
