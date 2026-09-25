import base64
from unittest.mock import MagicMock, patch

import pytest
from interactive import ssh
from interactive.ssh import parse_ed25519_public_key, sshd_config_text, fingerprint_sha256, ssh_enabled


def _key():
    raw = b"\x00\x00\x00\x0bssh-ed25519" + b"\x00\x00\x00 " + bytes(range(32))
    return "ssh-ed25519 " + base64.b64encode(raw).decode()


def test_key_parser_strict():
    line = _key()
    assert parse_ed25519_public_key(line) == line
    assert parse_ed25519_public_key(line + " comment") == line
    for bad in ("ssh-rsa AAAA", "ssh-ed25519 not-base64!!", "ssh-ed25519 " + "A" * 200, "", None, 123):
        with pytest.raises(ValueError):
            parse_ed25519_public_key(bad)


def test_sshd_config_restrictions():
    text = sshd_config_text()
    for needle in ("PermitRootLogin no", "PasswordAuthentication no",
                   "KbdInteractiveAuthentication no", "AllowUsers dml",
                   "X11Forwarding no", "AllowAgentForwarding no",
                   "AllowTcpForwarding local", "PermitTunnel no",
                   "PermitUserEnvironment no", "Port 2222",
                   "ListenAddress 127.0.0.1", "Subsystem sftp",
                   "ForceCommand /usr/local/bin/dml-ssh-session"):
        assert needle in text
    assert "PermitRootLogin yes" not in text
    assert "GatewayPorts" not in text


def test_fingerprint_stable():
    line = _key()
    assert fingerprint_sha256(line).startswith("SHA256:")
    assert fingerprint_sha256(line) == fingerprint_sha256(line)


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("INTERACTIVE_ALLOW_SSH", raising=False)
    assert ssh_enabled() is False
    monkeypatch.setenv("INTERACTIVE_ALLOW_SSH", "1")
    assert ssh_enabled() is True


def test_ssh_smoke_uses_portable_loopback_probe():
    calls = []

    def execute(_container, args, user="root"):
        calls.append((args, user))
        if args[:3] == ["stat", "-c", "%u"] or args[:3] == ["id", "-u", "dml"]:
            return 0, b"10001\n"
        if args == ["cat", ssh.SSH_HOST_PUB]:
            return 0, b"ssh-ed25519 host-key\n"
        return 0, b""

    with patch.object(ssh, "_exec", side_effect=execute):
        ssh.ssh_smoke(object(), "ssh-ed25519 host-key")
    probe = calls[-1][0]
    assert probe[:2] == ["python3", "-c"]
    assert "127.0.0.1" in probe[2] and "2222" in probe[2]
    assert all("/dev/tcp" not in " ".join(args) for args, _ in calls)


def test_ssh_image_capable_accepts_config_and_labels_shapes():
    from interactive.docker_ops import DockerOps, ssh_image_capable, SSH_PROFILE_LABEL
    from unittest.mock import MagicMock
    labels = {SSH_PROFILE_LABEL: "v1"}
    # Manager passes image_labels(image) which is the unwrapped labels dict.
    assert ssh_image_capable(labels) is True
    # Direct Config shape also accepted.
    assert ssh_image_capable({"Labels": labels}) is True
    assert ssh_image_capable({"Labels": {}}) is False
    assert ssh_image_capable(labels | {SSH_PROFILE_LABEL: "v0"}) is False
    assert ssh_image_capable({}) is False
    assert ssh_image_capable(None) is False
    # End-to-end: image_labels output must be recognised as capable.
    ops = DockerOps(MagicMock(), "worker", MagicMock())
    ops.client.images.get.return_value.attrs = {"Config": {"Labels": labels}}
    assert ssh_image_capable(ops.image_labels("sha256:abc")) is True


def test_setup_workload_sshd_creates_privsep_dir():
    # Fresh workload containers have an empty /run tmpfs: without /run/sshd,
    # `sshd -T` exits 255 ("Missing privilege separation directory").
    from interactive import ssh as _ssh
    calls = []

    def fake_exec(_container, args, user="root"):
        calls.append((tuple(args), user))
        if args[:2] == ["sshd", "-T"]:
            assert ("mkdir", "-p", "/run/sshd") in [tuple(c[0]) for c in calls], \
                "privsep dir must be created before sshd -T"
            return 0, b"permitrootlogin no\npasswordauthentication no\nport 2222\nallowusers dml\nx11forwarding no\nforcecommand /usr/local/bin/dml-ssh-session\n"
        if args == ["cat", _ssh.SSH_HOST_PUB]:
            return 0, b"ssh-ed25519 AAAA\n"
        if args[:1] == ["cat"]:
            return 0, b""
        return 0, b""

    with patch.object(_ssh, "_exec", side_effect=fake_exec):
        _ssh.setup_workload_sshd(object())
    flat = [c[0] for c in calls]
    assert ("mkdir", "-p", "/run/sshd") in flat
    assert ("chmod", "0755", "/run/sshd") in flat


class _FragmentedSocket:
    def __init__(self, data):
        self.data = bytearray(data)
        self.timeout = "unset"

    def settimeout(self, value):
        self.timeout = value

    def recv(self, size):
        count = min(size, 3, len(self.data))
        result = bytes(self.data[:count])
        del self.data[:count]
        return result


def _frame(stream, payload):
    return bytes([stream, 0, 0, 0]) + len(payload).to_bytes(4, "big") + payload


def test_relay_command_compiles_and_demultiplexes_docker_frames():
    api = MagicMock()
    api.exec_create.return_value = {"Id": "exec-id"}
    raw = _FragmentedSocket(_frame(2, b"internal error") + _frame(1, b"SSH-2.0-test\r\n"))
    api.exec_start.return_value._sock = raw
    relay = ssh.SshRelay(api, "container-id")
    relay.connect()
    command = api.exec_create.call_args.kwargs["cmd"]
    assert command[:2] == ["python3", "-c"]
    compile(command[2], "docker-ssh-bridge", "exec")
    assert "127.0.0.1" in command[2]
    assert raw.timeout is None  # Idle SSH connections must remain open.
    assert relay.recv(4) == b"SSH-"
    assert relay.recv() == b"2.0-test\r\n"
    assert relay.recv() == b""


def test_relay_rejects_truncated_frame():
    relay = ssh.SshRelay(None, "container-id")
    relay.sock = _FragmentedSocket(_frame(1, b"hello")[:-2])
    with pytest.raises(RuntimeError, match="truncated"):
        relay.recv()
