"""Portable, secret-free Docker Compose E2E harness with unconditional cleanup."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).parent


def safe_logs(value, sensitive):
    for secret in sensitive:
        value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[REDACTED-TICKET]", value)
    value = re.sub(r"(?i)(bearer\s+|(?:auth[-_]?key|ticket|key)\s*[=:]\s*)[^\s,\"}]+", r"\1[REDACTED]", value)
    value = re.sub(r"[A-Za-z0-9_-]{40,}", "[REDACTED-LONG-VALUE]", value)
    return value[-100000:]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="interactive-" + os.getenv("GITHUB_RUN_ID", secrets.token_hex(5)) + "-" + os.getenv("GITHUB_RUN_ATTEMPT", "1"))
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--deliberate-failure", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"interactive-[a-z0-9-]{1,60}", args.project):
        raise ValueError("invalid isolated project name")
    if args.cleanup:
        env = {**os.environ, "FIXTURE_STATE": "/tmp/interactive-cleanup-unused", "FIXTURE_RESULTS": "/tmp/interactive-cleanup-unused",
               "FIXTURE_SECRET": "cleanup-placeholder", "MANAGEMENT_IMAGE": args.project + "-management",
               "GATEWAY_IMAGE": args.project + "-gateway", "FIXTURE_IMAGE": args.project + "-fixture"}
        return subprocess.run(["docker", "compose", "--project-name", args.project, "--file", str(FIXTURE / "compose.yaml"),
                               "down", "--volumes", "--remove-orphans"], env=env, timeout=60).returncode
    results = ROOT / "artifacts" / args.project
    results.mkdir(parents=True, exist_ok=True)
    sensitive = []
    status = 1
    with tempfile.TemporaryDirectory(prefix=args.project + "-") as tmp:
        state = Path(tmp)
        state.chmod(0o755)
        for name in ("certs", "secrets", "driver-secrets"):
            (state / name).mkdir()
        for name in ("controller", "gateway", "bootstrap", "fixture", "user-a", "user-b"):
            value = secrets.token_urlsafe(40)
            sensitive.append(value)
            (state / "secrets" / name).write_text(value)
            if name in ("fixture", "user-a", "user-b"):
                (state / "driver-secrets" / name).write_text(value)
        encryption = Fernet.generate_key().decode()
        sensitive.append(encryption)
        (state / "secrets/encryption").write_text(encryption)
        private = Ed25519PrivateKey.generate()
        pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        sensitive.append(pem)
        (state / "secrets/signing.pem").write_text(pem)
        public = private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        (state / "secrets/public.json").write_text(json.dumps({"primary": {"pem": public}}))
        env = {**os.environ, "FIXTURE_UID": str(os.getuid()), "FIXTURE_GID": str(os.getgid()), "FIXTURE_STATE": str(state), "FIXTURE_RESULTS": str(results),
            "FIXTURE_SECRET": (state / "secrets/fixture").read_text(),
            "MANAGEMENT_IMAGE": args.project + "-management", "GATEWAY_IMAGE": args.project + "-gateway", "FIXTURE_IMAGE": args.project + "-fixture"}
        base = ["docker", "compose", "--project-name", args.project, "--file", str(FIXTURE / "compose.yaml")]
        def execute(command, timeout=90, check=True):
            result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=timeout)
            if check and result.returncode:
                (results / "command-failure.txt").write_text(safe_logs(result.stdout + result.stderr, sensitive))
                raise RuntimeError("fixture command failed; see sanitized diagnostics")
            return result
        def compose(*command, **kwargs):
            return execute(base + list(command), **kwargs)
        try:
            execute(["docker", "info"], timeout=10)
            version = json.loads(execute(["docker", "version", "--format", "{{json .}}"], timeout=10).stdout)
            compose_version = execute(["docker", "compose", "version", "--short"], timeout=10).stdout.strip()
            metadata = {"docker_client": version["Client"]["Version"], "docker_server": version["Server"]["Version"],
                        "architecture": version["Server"]["Arch"], "compose": compose_version}
            (results / "versions.json").write_text(json.dumps(metadata, indent=2))
            execute(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(state / "certs/server.key"),
                     "-out", str(state / "certs/server.crt"), "-days", "1", "-subj", "/CN=headscale",
                     "-addext", "subjectAltName=DNS:headscale"], timeout=10)
            (state / "certs/server.key").chmod(0o644)
            compose("config", "--quiet")
            compose("pull", "headscale", "tailscale", timeout=120)
            compose("build", "management", "gateway", "controller", timeout=240)
            compose("up", "--detach", "headscale")
            deadline = time.monotonic() + 45
            while True:
                response = compose("exec", "-T", "headscale", "headscale", "apikeys", "create", "--expiration", "1h", check=False)
                if response.returncode == 0:
                    admin = response.stdout.strip()
                    sensitive.append(admin)
                    (state / "secrets/headscale").write_text(admin)
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("disposable Headscale startup deadline exceeded")
                time.sleep(0.25)
            sentinel_key = json.loads(compose("exec", "-T", "headscale", "headscale", "preauthkeys", "create",
                "--tags", "tag:interactive-endpoint", "--expiration", "1h", "--output", "json").stdout)
            value = sentinel_key["key"]
            sensitive.append(value)
            (state / "certs/sentinel.auth").write_text(value)
            compose("up", "--detach", "--wait", "--wait-timeout", "30", "sentinel")
            compose("exec", "-T", "sentinel", "tailscale", "up", "--login-server=https://headscale:8080",
                    "--auth-key=file:/certs/sentinel.auth", "--hostname=sentinel", "--accept-dns=false", "--timeout=20s")
            compose("run", "--rm", "--no-deps", "migrate")
            compose("up", "--detach", "--wait", "--wait-timeout", "60", "management", "tailscale")
            compose("run", "--rm", "--no-deps", "bootstrap", timeout=110)
            compose("up", "--detach", "--wait", "--wait-timeout", "60", "gateway")
            first_node = compose("exec", "-T", "tailscale", "tailscale", "status", "--json").stdout
            first_identity = json.loads(first_node)["Self"]["ID"]
            compose("stop", "--timeout", "5", "gateway", "management")
            compose("run", "--rm", "--no-deps", "migrate")
            compose("up", "--detach", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "60", "management")
            compose("up", "--detach", "--no-deps", "--wait", "--wait-timeout", "60", "tailscale")
            compose("run", "--rm", "--no-deps", "bootstrap", timeout=110)
            compose("up", "--detach", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "60", "gateway")
            second_identity = json.loads(compose("exec", "-T", "tailscale", "tailscale", "status", "--json").stdout)["Self"]["ID"]
            if first_identity != second_identity:
                raise RuntimeError("persistent gateway identity changed across deployment")
            # Sidecar config/image replacement requires a new gateway namespace.
            compose("stop", "--timeout", "5", "gateway")
            compose("up", "--detach", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "60", "tailscale")
            compose("run", "--rm", "--no-deps", "bootstrap", timeout=110)
            compose("up", "--detach", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "60", "gateway")
            gateway_id = compose("ps", "--quiet", "gateway").stdout.strip()
            sidecar_id = compose("ps", "--quiet", "tailscale").stdout.strip()
            namespace = execute(["docker", "inspect", "--format", "{{.HostConfig.NetworkMode}}", gateway_id]).stdout.strip()
            if namespace != "container:" + sidecar_id:
                raise RuntimeError("gateway retained stale sidecar namespace")
            print("Disposable redeployment: persistent identity and current gateway namespace verified", flush=True)
            compose("up", "--detach", "controller", "a", "a-agent", "a-echo", "b", "b-agent", "b-echo",
                    "replacement", "replacement-agent", "replacement-echo", "fresh", "fresh-agent", "gateway-agent", "gateway-canary")
            command = ["run", "--rm", "--no-deps", "driver", "python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_enrollment.py", "test_gateway.py", "test_isolation.py", "test_lifecycle.py", "test_faults.py", "--junitxml=/results/junit.xml"]
            if args.deliberate_failure:
                command += ["--deliberate-failure", "-k", "deliberate_failure"]
            stop_faults = threading.Event()
            def faults():
                seen = set()
                allowed = {"stop-management": ("stop", "--timeout", "5", "management"),
                           "start-management": ("up", "--detach", "--no-deps", "--wait", "--wait-timeout", "30", "management"),
                           "stop-headscale": ("stop", "--timeout", "5", "headscale"),
                           "start-headscale": ("up", "--detach", "--no-deps", "headscale")}
                while not stop_faults.wait(0.1):
                    request = results / "fault-request.json"
                    if not request.exists():
                        continue
                    try:
                        record = json.loads(request.read_text())
                        if record["id"] in seen:
                            continue
                        seen.add(record["id"])
                        action = allowed[record["action"]]
                        result = compose(*action, timeout=45, check=False)
                        response = {"id": record["id"], "ok": result.returncode == 0}
                        temporary = results / "fault-response.tmp"
                        temporary.write_text(json.dumps(response))
                        temporary.replace(results / "fault-response.json")
                    except Exception:
                        print("Secondary fault orchestration failure", flush=True)
            worker = threading.Thread(target=faults, daemon=True)
            worker.start()
            try:
                test = compose(*command, timeout=300, check=False)
            finally:
                stop_faults.set()
                worker.join(timeout=50)
            status = test.returncode
            if status == 0:
                response = compose("exec", "-T", "headscale", "headscale", "nodes", "list", "--output", "json").stdout
                nodes = json.loads(response)
                if isinstance(nodes, dict):
                    nodes = nodes.get("nodes", [])
                nodes = nodes or []
                if not any(node.get("name") == "sentinel" for node in nodes):
                    raise RuntimeError("unrelated sentinel disappeared during exact-ID cleanup")
                print("Cleanup isolation: unrelated sentinel survived managed resource revocation", flush=True)
            output = safe_logs(test.stdout + test.stderr, sensitive)
            print(output, flush=True)
            (results / "pytest.txt").write_text(output)
        except Exception as error:
            print("Interactive E2E failed: " + type(error).__name__ + "; see sanitized artifacts", flush=True)
            status = 1
        finally:
            for filename, command in (("status.txt", ["ps", "--all"]), ("services.log", ["logs", "--no-color", "--tail", "100", "management", "gateway", "bootstrap", "controller"])):
                try:
                    result = compose(*command, timeout=15, check=False)
                    (results / filename).write_text(safe_logs(result.stdout + result.stderr, sensitive))
                except Exception:
                    print("Secondary diagnostic collection failure", flush=True)
            try:
                response = compose("exec", "-T", "headscale", "headscale", "nodes", "list", "--output", "json", timeout=10, check=False)
                if response.returncode == 0:
                    nodes = json.loads(response.stdout)
                    if isinstance(nodes, dict):
                        nodes = nodes.get("nodes", [])
                    nodes = nodes or []
                    summary = [{**{key: node.get(key) for key in ("id", "name", "tags", "online", "ip_addresses")},
                                "key_id": (node.get("pre_auth_key") or node.get("preAuthKey") or {}).get("id"),
                                "ephemeral": (node.get("pre_auth_key") or node.get("preAuthKey") or {}).get("ephemeral")} for node in nodes]
                    (results / "nodes.json").write_text(json.dumps(summary, indent=2))
                (results / "policy.json").write_text((FIXTURE / "policy.hujson").read_text())
            except Exception:
                print("Secondary node/policy diagnostic failure", flush=True)
            try:
                cleanup = compose("down", "--volumes", "--remove-orphans", timeout=60, check=False)
                if cleanup.returncode:
                    print("Secondary project cleanup failure", flush=True)
                    if status == 0:
                        status = 1
            except Exception:
                print("Secondary project cleanup failure", flush=True)
                if status == 0:
                    status = 1
            # JUnit failure text also needs sanitization before upload.
            junit = results / "junit.xml"
            if junit.exists():
                clean = junit.with_suffix(".sanitized.xml")
                clean.write_text(safe_logs(junit.read_text(), sensitive))
                clean.replace(junit)
            for artifact in results.iterdir():
                if artifact.is_file() and any(value and value in artifact.read_text(errors="replace") for value in sensitive):
                    raise RuntimeError("artifact redaction invariant failed")
    return status


if __name__ == "__main__":
    sys.exit(main())
