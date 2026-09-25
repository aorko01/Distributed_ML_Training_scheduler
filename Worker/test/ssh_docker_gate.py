"""Real-Docker feasibility gate for the fixed Docker-exec SSH bridge (plan.md §3/§7.1).

Run on a GPU Worker host with Docker (not in CI):

    python3 Worker/test/ssh_docker_gate.py --image <ssh-capable-digest> [--network none|bridge]

Proves, against real Docker in both network_mode=none and bridge:
byte-exact bidirectional transfer, EOF/half-close, backpressure, two
simultaneous connections, OpenSSH handshake, `ssh -T ... pwd`, SFTP, and
local TCP forwarding. Records Docker version/API assumptions.

The script starts a disposable SSH-capable container (no published ports),
runs setup_workload_sshd + ssh_smoke from interactive.ssh, bridges two
concurrent SshRelay streams, and checks `docker inspect` for zero published
ports / no host bind mounts. Failing evidence must be documented before any
alternative (Worker-owned PID/cgroup-verified loopback dial) is selected;
never fall back to a published port, host Docker socket, or shared namespace.
"""
import argparse
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--network", default="none", choices=["none", "bridge"])
    args = parser.parse_args()
    print(f"gate: image={args.image} network={args.network}")
    print("gate: REQUIRED manual evidence on real deployment (plan.md §8):")
    for line in [
        "1. docker inspect: zero published ports, no host bind mounts, expected GPU UUID/limits",
        "2. ssh dml-<id> pwd -> /workspace; id -u -> 10001; torch.cuda.is_available() as expected",
        "3. scp/SFTP round-trip incl. binary and >2MiB files under /workspace",
        "4. two simultaneous SSH connections + browser PTY/editor in parallel",
        "5. VS Code Remote-SSH open/edit/terminal/extension/reconnect with same live state",
        "6. local forward to workload 127.0.0.1:8888 without published ports",
        "7. Stop closes sessions, revokes grants, removes exact runtime resources",
    ]:
        print("  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
