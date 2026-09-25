import base64
import pytest
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
                   "ListenAddress 127.0.0.1", "Subsystem sftp"):
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
