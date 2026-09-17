"""Disposable M1 spike. Does not use the installed Headscale daemon or tailnet.

Run from the repository root: python3 test/interactive_e2e/compatibility.py
Requires Docker Compose, OpenSSL and PyYAML. Secrets are captured, never printed.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import secrets
import ssl
import subprocess
import tempfile
import time
import urllib.request

import yaml

ROOT = Path(__file__).resolve().parents[2]
HEADSCALE = "headscale/headscale:0.29.3@sha256:0e7f1c6e4ce6c2a2a001103ecd3fa645a045adf30ac8a5234fe037b43000cd72"
TAILSCALE = "tailscale/tailscale:v1.102.3@sha256:8c42c4574ab066384fcb72f69e086a2ff1dd3652eb6f56856cee34bcf0d2f680"
PYTHON = "python:3.11.14-slim-bookworm@sha256:65a93d69fa75478d554f4ad27c85c1e69fa184956261b4301ebaf6dbb0a3543d"


def command(args, timeout=90, check=True, input=None):
    result = subprocess.run(args, input=input, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        # Commands may contain runtime keys. Never interpolate argv or output.
        raise RuntimeError(f"fixture command failed (exit {result.returncode})")
    return result


def poll(fn, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = fn()
            if value:
                return value
        except (OSError, RuntimeError, ValueError):
            pass
        time.sleep(0.25)
    raise TimeoutError("fixture readiness deadline exceeded")


SOCKS_CHECK = r'''
import socket, struct, sys
ip, port = sys.argv[1], int(sys.argv[2])
s = socket.create_connection(("127.0.0.1", 1055), timeout=4)
s.settimeout(4)
def exact(n):
    data = b""
    while len(data) < n:
        part = s.recv(n-len(data))
        if not part: raise RuntimeError("EOF")
        data += part
    return data
try:
    s.sendall(b"\x05\x01\x00")
    assert exact(2) == b"\x05\x00"
    s.sendall(b"\x05\x01\x00\x01" + socket.inet_aton(ip) + struct.pack("!H", port))
    h = exact(4)
    assert h[1] == 0
    exact({1:4,4:16}[h[3]] + 2)
    prefix = exact(len(b"endpoint:"))
    assert prefix == b"endpoint:"
    payload = b"\x00\xff\x80" + bytes(range(256)) * 8
    s.sendall(payload)
    assert exact(len(payload)) == payload
    print("TCP_BINARY_OK")
finally:
    s.close()
'''


def main():
    project = "interactive-spike-" + secrets.token_hex(5)
    with tempfile.TemporaryDirectory(prefix=project) as tmp:
        state = Path(tmp)
        os.chmod(state, 0o755)  # containers need to traverse certificate/config mount
        cert = state / "server.crt"
        key = state / "server.key"
        command(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                 "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=headscale",
                 "-addext", "subjectAltName=DNS:headscale,DNS:localhost,IP:127.0.0.1"])
        # This key is disposable and only mounted into the disposable Headscale.
        os.chmod(key, 0o644)
        config = {
            "server_url": "https://headscale:8080", "listen_addr": "0.0.0.0:8080",
            "metrics_listen_addr": "127.0.0.1:9090", "grpc_listen_addr": "127.0.0.1:50443",
            "grpc_allow_insecure": False,
            "noise": {"private_key_path": "/var/lib/headscale/noise.key"},
            "prefixes": {"v4": "100.64.0.0/10", "v6": "fd7a:115c:a1e0::/48", "allocation": "sequential"},
            "derp": {"server": {"enabled": True, "region_id": 999, "region_code": "fixture",
                                "region_name": "Private fixture", "verify_clients": True,
                                "stun_listen_addr": "0.0.0.0:3478",
                                "private_key_path": "/var/lib/headscale/derp.key",
                                "automatically_add_embedded_derp_region": True},
                     "urls": [], "paths": [], "auto_update_enabled": False},
            "disable_check_updates": True,
            "database": {"type": "sqlite", "sqlite": {"path": "/var/lib/headscale/db.sqlite", "write_ahead_log": True}},
            "tls_cert_path": "/fixture/server.crt", "tls_key_path": "/fixture/server.key",
            "policy": {"mode": "file", "path": "/fixture/policy.hujson"},
            "dns": {"magic_dns": False, "base_domain": "fixture.invalid", "override_local_dns": False,
                    "nameservers": {"global": [], "split": {}}},
            "unix_socket": "/var/run/headscale/headscale.sock", "unix_socket_permission": "0770",
            "log": {"level": "warn", "format": "json"}, "logtail": {"enabled": False},
        }
        (state / "config.yaml").write_text(yaml.safe_dump(config))
        (state / "policy.hujson").write_text((Path(__file__).parent / "policy.hujson").read_text())
        services = {
            "headscale": {"image": HEADSCALE, "command": ["serve"],
                          "ports": ["127.0.0.1::8080"],
                          "volumes": [f"{state}:/fixture:ro", f"{state}/config.yaml:/etc/headscale/config.yaml:ro", "headscale-state:/var/lib/headscale"],
                          "networks": ["transport"]}
        }
        for name in ("gateway", "a", "b"):
            services[name] = {"image": TAILSCALE, "entrypoint": ["tailscaled"],
                              "command": ["--tun=userspace-networking", "--state=/state/tailscaled.state",
                                          "--socket=/var/run/tailscale/tailscaled.sock", "--socks5-server=127.0.0.1:1055"],
                              "environment": {"SSL_CERT_FILE": "/fixture/server.crt", "TS_NO_LOGS_NO_SUPPORT": "true"},
                              "volumes": [f"{state}:/fixture:ro", f"{name}-state:/state"],
                              "networks": ["transport"]}
            services[name + "-echo"] = {"image": PYTHON, "network_mode": "service:" + name,
                                        "command": ["python", "/fixture/echo_endpoint.py"],
                                        "volumes": [f"{Path(__file__).parent / 'fixtures' / 'echo_endpoint.py'}:/fixture/echo_endpoint.py:ro"]}
        compose = {"services": services, "networks": {"transport": {}},
                   "volumes": {name: {} for name in ("headscale-state", "gateway-state", "a-state", "b-state")}}
        manifest = state / "compose.yaml"
        manifest.write_text(yaml.safe_dump(compose))
        base = ["docker", "compose", "--project-name", project, "--file", str(manifest)]
        def cli(*args, **kwargs):
            return command(base + list(args), **kwargs)
        def hs(*args):
            return cli("exec", "-T", "headscale", "headscale", *args).stdout
        def ts(name, *args):
            return cli("exec", "-T", name, "tailscale", *args).stdout
        try:
            cli("up", "--detach", timeout=120)
            try:
                admin = poll(lambda: hs("apikeys", "create", "--expiration", "1h").strip())
            except TimeoutError:
                # No credentials exist at this startup stage.
                print(cli("logs", "--tail", "20", "headscale", check=False).stdout)
                raise
            binding = cli("port", "headscale", "8080").stdout.strip()
            url = "https://localhost:" + binding.rsplit(":", 1)[1]
            context = ssl.create_default_context(cafile=str(cert))
            def api(path, payload=None):
                request = urllib.request.Request(url + "/api/v1/" + path,
                    data=json.dumps(payload).encode() if payload is not None else None,
                    headers={"Authorization": "Bearer " + admin, "Content-Type": "application/json"})
                with urllib.request.urlopen(request, context=context, timeout=5) as response:
                    return json.load(response)
            keys = {}
            for name in ("gateway", "a", "b"):
                tag = "tag:interactive-" + ("gateway" if name == "gateway" else "endpoint")
                expiry = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5)).isoformat()
                keys[name] = api("preauthkey", {"reusable": False, "ephemeral": name != "gateway",
                                               "expiration": expiry, "acl_tags": [tag]})["preAuthKey"]
                ts(name, "up", "--login-server=https://headscale:8080", "--auth-key=" + keys[name]["key"],
                   "--hostname=" + name, "--accept-dns=false", "--timeout=30s")
                ts(name, "serve", "--bg", "--tcp=9000", "tcp://127.0.0.1:9000")
                ts(name, "serve", "--bg", "--tcp=9001", "tcp://127.0.0.1:9001")
            nodes = poll(lambda: api("node").get("nodes") if len(api("node").get("nodes", [])) == 3 else None)
            by_name = {node["name"]: node for node in nodes}
            for name, node in by_name.items():
                assert str(node["preAuthKey"]["id"]) == str(keys[name]["id"]), "missing exact key association"
                assert node["tags"] == keys[name]["aclTags"], "tag association mismatch"
                assert node["preAuthKey"]["ephemeral"] == (name != "gateway")
            print("Pinned API: tagged node/key correlation and role attributes verified")
            def tcp(source, target, port=9000):
                container = cli("ps", "--quiet", source).stdout.strip()
                ip = next(ip for ip in by_name[target]["ipAddresses"] if ":" not in ip)
                return command(["docker", "run", "--rm", "--network", "container:" + container,
                                PYTHON, "python", "-c", SOCKS_CHECK, ip, str(port)], timeout=15, check=False)
            poll(lambda: tcp("gateway", "b").returncode == 0)
            print("Userspace SOCKS + TCP Serve: exact binary roundtrip verified")
            assert tcp("a", "b").returncode != 0, "peer initiation unexpectedly allowed"
            assert tcp("a", "gateway").returncode != 0, "reverse initiation unexpectedly allowed"
            assert tcp("gateway", "b", 9001).returncode != 0, "unallowlisted port unexpectedly allowed"
            # Independently prove both denied canaries actually listen locally.
            for name in ("gateway", "b"):
                container = cli("ps", "--quiet", name).stdout.strip()
                command(["docker", "run", "--rm", "--network", "container:" + container, PYTHON,
                         "python", "-c", "import socket; socket.create_connection(('127.0.0.1',9001),3).close()"])
            assert tcp("gateway", "b").returncode == 0, "positive control failed after denial"
            print("Policy: peer/reverse/port denial with listening canaries and positive controls verified")
            policy = api("policy")
            assert "policy" in policy
            print("Pinned policy read API verified")
            digests = {image: command(["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", image]).stdout.strip()
                       for image in (HEADSCALE, TAILSCALE, PYTHON)}
            evidence = {"headscale": "0.29.3", "tailscale": "1.102.3", "digests": digests,
                        "correlation": "preAuthKey.id + exact tags", "transport": "userspace SOCKS5 + TCP Serve",
                        "policy": "grants; gateway to endpoint TCP 9000 only", "platform": "local Docker"}
            (Path(__file__).parent / "compatibility-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
        finally:
            result = cli("down", "--volumes", "--remove-orphans", timeout=60, check=False)
            if result.returncode:
                print("Disposable project cleanup failed", flush=True)
                raise RuntimeError("fixture cleanup failed")


if __name__ == "__main__":
    main()
