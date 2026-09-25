"""Workload-local OpenSSH supervision and fixed Docker-exec byte bridge.

Plan.md §3-§5: sshd runs INSIDE the exact verified workload container as
root, bound only to workload 127.0.0.1:2222. No Docker ports, no host
listener, no shared namespace. Each approved WSS stream gets a fixed
Docker-exec relay (non-TTY, byte-clean) to that loopback daemon.
"""
import base64
import logging
import os
import re
import secrets

log = logging.getLogger("ssh")

SSH_PORT = 2222
SSH_USER = "dml"
SSH_UID = 10001
SSH_DIR = "/run/dml-vscode-ssh"
SSH_HOST_KEY = SSH_DIR + "/ssh_host_ed25519_key"
SSH_HOST_PUB = SSH_DIR + "/ssh_host_ed25519_key.pub"
SSH_AUTH_KEYS = SSH_DIR + "/authorized_keys"
SSH_CONFIG = SSH_DIR + "/sshd_config"
SSH_PID = SSH_DIR + "/sshd.pid"

SSH_PROFILE_LABEL = "io.dml.vscode-ssh-profile"
SSH_PROFILE_VERSION = "v1"

# Strict Ed25519 public key: "ssh-ed25519 <base64-68chars> [comment]".
_PUBKEY_RE = re.compile(r"^ssh-ed25519 [A-Za-z0-9+/]{67}[A-Za-z0-9+/=]$")
_MAX_KEYS = 16


def ssh_enabled() -> bool:
    return os.getenv("INTERACTIVE_ALLOW_SSH", "").strip().lower() in ("1", "true", "yes")


def parse_ed25519_public_key(value) -> str:
    """Validate one Ed25519 public key with strict length/format bounds."""
    if not isinstance(value, str):
        raise ValueError("invalid key")
    text = value.strip()
    if len(text) > 1024:
        raise ValueError("key too long")
    head = " ".join(text.split()[:2])
    if not _PUBKEY_RE.fullmatch(head):
        raise ValueError("unsupported key type/format")
    try:
        raw = base64.b64decode(head.split(" ", 1)[1], validate=True)
    except Exception:
        raise ValueError("invalid key encoding") from None
    # ssh wire format: string "ssh-ed25519" (11) + string 32-byte key = 51 bytes.
    if len(raw) != 51 or raw[0:4] != b"\x00\x00\x00\x0b" or raw[4:15] != b"ssh-ed25519":
        raise ValueError("invalid key blob")
    return head


def fingerprint_sha256(public_key_line: str) -> str:
    import hashlib
    raw = base64.b64decode(public_key_line.split()[1], validate=True)
    return "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()


def sshd_config_text() -> str:
    """Minimal root-owned sshd config. Verified via `sshd -T` in tests."""
    return "\n".join([
        "Port 2222",
        "ListenAddress 127.0.0.1",
        "Protocol 2",
        "HostKey /run/dml-vscode-ssh/ssh_host_ed25519_key",
        "PidFile /run/dml-vscode-ssh/sshd.pid",
        "AuthorizedKeysFile /run/dml-vscode-ssh/authorized_keys",
        "PasswordAuthentication no",
        "KbdInteractiveAuthentication no",
        "ChallengeResponseAuthentication no",
        "PubkeyAuthentication yes",
        "PermitRootLogin no",
        "AllowUsers dml",
        "ForceCommand /usr/local/bin/dml-ssh-session",
        "PermitEmptyPasswords no",
        "X11Forwarding no",
        "AllowAgentForwarding no",
        "AllowTcpForwarding local",
        "PermitTunnel no",
        "AllowStreamLocalForwarding no",
        "PermitUserEnvironment no",
        "PermitUserRC no",
        "ClientAliveInterval 30",
        "ClientAliveCountMax 4",
        "LoginGraceTime 30",
        "MaxStartups 4:30:8",
        "MaxSessions 8",
        "MaxAuthTries 4",
        "Subsystem sftp /usr/lib/openssh/sftp-server",
        "AcceptEnv LANG LC_*",
        "",
    ])


def _exec(container, args, user="root"):
    result = container.exec_run(args, user=user, demux=False)
    code = getattr(result, "exit_code", result[0] if isinstance(result, tuple) else 1)
    output = getattr(result, "output", result[1] if isinstance(result, tuple) else b"")
    if isinstance(output, tuple):
        output = b"".join(x or b"" for x in output)
    return code, output or b""


def setup_workload_sshd(container) -> dict:
    """Generate tmpfs keys/config inside workload, start loopback sshd.

    Returns {"host_public_key": ..., "fingerprint": ...}. Raises on failure.
    Fixed argument vectors only; no client input reaches a shell.
    """
    steps = [
        ["mkdir", "-p", SSH_DIR],
        ["chmod", "0755", SSH_DIR],
        # OpenSSH privilege-separation dir: /run is an empty tmpfs in fresh
        # workload containers, so sshd -T and the daemon both fail with
        # "Missing privilege separation directory: /run/sshd" without this.
        ["mkdir", "-p", "/run/sshd"],
        ["chmod", "0755", "/run/sshd"],
        # Debian/Ubuntu useradd leaves dml password-locked (`!`); this
        # sshd build denies locked accounts for ALL methods including
        # pubkey ("User dml not allowed because account is locked"). `*`
        # keeps password login impossible while allowing pubkey.
        ["usermod", "-p", "*", "dml"],
        ["rm", "-f", SSH_HOST_KEY, SSH_HOST_PUB, SSH_CONFIG, SSH_PID],
    ]
    for args in steps:
        code, _ = _exec(container, args)
        if code != 0:
            raise RuntimeError("ssh setup failed")
    code, _ = _exec(container, ["ssh-keygen", "-t", "ed25519", "-N", "",
                                "-f", SSH_HOST_KEY, "-C", "dml-runtime"])
    if code != 0:
        raise RuntimeError("host keygen failed")
    for args in (["chmod", "0600", SSH_HOST_KEY], ["chmod", "0644", SSH_HOST_PUB],
                 ["touch", SSH_AUTH_KEYS], ["chmod", "0600", SSH_AUTH_KEYS],
                 ["chown", "0:0", SSH_DIR, SSH_HOST_KEY, SSH_HOST_PUB],
                 # Keys file is user-owned: this sshd opens it as dml, so
                 # root-owned 600 fails with "Could not open ... authorized
                 # keys: Permission denied". 600 dml satisfies StrictModes too.
                 ["chown", "10001:10001", SSH_AUTH_KEYS]):
        code, _ = _exec(container, args)
        if code != 0:
            raise RuntimeError("ssh perms failed")
    # Write config via base64 to avoid shell quoting of client data (none here).
    import base64 as _b64
    payload = _b64.b64encode(sshd_config_text().encode()).decode()
    code, _ = _exec(container, ["sh", "-c",
        "base64 -d > %s <<'EOF'\n%s\nEOF\nchmod 0600 %s; chown 0:0 %s" % (SSH_CONFIG, payload, SSH_CONFIG, SSH_CONFIG)])
    if code != 0:
        raise RuntimeError("sshd config failed")
    code, out = _exec(container, ["sshd", "-T", "-f", SSH_CONFIG])
    if code != 0:
        raise RuntimeError("sshd -T rejected config")
    text = out.decode(errors="replace").lower()
    for needle in ("permitrootlogin no", "passwordauthentication no", "port 2222",
                   "allowusers dml", "x11forwarding no",
                   "forcecommand /usr/local/bin/dml-ssh-session"):
        if needle not in text:
            raise RuntimeError("sshd -T missing " + needle)
    code, _ = _exec(container, ["/usr/sbin/sshd", "-f", SSH_CONFIG])
    if code != 0:
        raise RuntimeError("sshd start failed")
    code, out = _exec(container, ["cat", SSH_HOST_PUB])
    if code != 0 or not out.strip():
        raise RuntimeError("host pubkey unreadable")
    pub = out.decode().strip().splitlines()[0]
    if not pub.startswith("ssh-ed25519 "):
        raise RuntimeError("unexpected host key type")
    return {"host_public_key": pub, "fingerprint": fingerprint_sha256(pub)}


def install_authorized_key(container, public_key: str) -> None:
    """Idempotently install one validated client pubkey via fixed helper."""
    clean = parse_ed25519_public_key(public_key)
    # Fixed helper: exact line append if missing, strict perms, bounded file.
    code, out = _exec(container, ["cat", SSH_AUTH_KEYS])
    existing = (out.decode(errors="replace") if code == 0 else "").splitlines()
    lines = [l.strip() for l in existing if l.strip()]
    if clean in lines:
        return
    if len(lines) >= _MAX_KEYS:
        raise ValueError("too many keys")
    import base64 as _b64
    payload = _b64.b64encode((clean + "\n").encode()).decode()
    code, _ = _exec(container, ["sh", "-c",
        "base64 -d >> %s <<'EOF'\n%s\nEOF\nchmod 0600 %s; chown 10001:10001 %s" % (SSH_AUTH_KEYS, payload, SSH_AUTH_KEYS, SSH_AUTH_KEYS)])
    if code != 0:
        raise RuntimeError("key install failed")
    # Re-verify no symlink swap / perms (user-owned: sshd opens it as dml).
    code, out = _exec(container, ["stat", "-c", "%a %U", SSH_AUTH_KEYS])
    if code != 0 or out.decode().strip() != "600 dml":
        raise RuntimeError("key file unsafe")


def ssh_smoke(container, host_public_key: str) -> None:
    """Readiness smoke: user/cwd/prereqs before publishing SSH-ready."""
    checks = [
        (["stat", "-c", "%u", "/workspace"], b"10001"),
        (["id", "-u", "dml"], b"10001"),
        (["test", "-x", "/usr/sbin/sshd"], None),
        (["test", "-x", "/usr/lib/openssh/sftp-server"], None),
        (["test", "-x", "/usr/local/bin/dml-ssh-session"], None),
        (["test", "-x", "/bin/bash"], None),
        (["test", "-x", "/usr/bin/tar"], None),
    ]
    for args, expect in checks:
        code, out = _exec(container, args, user="dml" if args[:2] == ["id", "-u"] else "root")
        if code != 0:
            raise RuntimeError("ssh smoke failed: " + " ".join(args))
        if expect is not None and out.strip() != expect:
            raise RuntimeError("ssh smoke mismatch")
    code, out = _exec(container, ["cat", SSH_HOST_PUB])
    if code != 0 or out.decode().strip().splitlines()[0] != host_public_key.strip():
        raise RuntimeError("host key changed within generation")
    code, _ = _exec(container, ["python3", "-c",
        "import socket; s = socket.create_connection(('127.0.0.1', 2222), 2); s.close()"])
    if code != 0:
        raise RuntimeError("sshd loopback unreachable")


def stop_workload_sshd(container) -> None:
    try:
        _exec(container, ["sh", "-c", "kill $(cat %s 2>/dev/null) 2>/dev/null; rm -rf %s; true" % (SSH_PID, SSH_DIR)])
    except Exception:
        pass


class SshRelay:
    """Fixed Docker-exec byte bridge to workload-loopback sshd.

    Non-TTY exec of a fixed Python TCP bridge. The Docker API multiplexes
    non-TTY stdout into framed messages, which recv() decodes before passing
    the SSH bytes to the client.
    """

    def __init__(self, docker_api, container_id):
        self.api = docker_api
        self.container_id = container_id
        self.exec_id = None
        self.sock = None
        self._pending = bytearray()

    def connect(self):
        # Fixed vector, no user input: python3 bridges stdio to 127.0.0.1:2222.
        bridge = """import os
import select
import socket

s = socket.create_connection(('127.0.0.1', 2222), timeout=5)
s.settimeout(None)
readers = [0, s]
while True:
    ready, _, _ = select.select(readers, [], [])
    if s in ready:
        data = s.recv(32768)
        if not data:
            break
        view = memoryview(data)
        while view:
            view = view[os.write(1, view):]
    if 0 in ready:
        data = os.read(0, 32768)
        if data:
            s.sendall(data)
        else:
            readers.remove(0)
            s.shutdown(socket.SHUT_WR)
s.close()
"""
        self.exec_id = self.api.exec_create(
            self.container_id, cmd=["python3", "-c", bridge],
            stdin=True, stdout=True, stderr=False, tty=False,
            privileged=False, user="root",
            workdir="/",
            environment={})["Id"]
        stream = self.api.exec_start(self.exec_id, tty=False, socket=True, demux=False)
        # Non-TTY Docker exec output is framed even with stderr disabled.
        self.sock = stream._sock
        self.sock.settimeout(None)
        self._stream = stream

    def _read_exact(self, size):
        data = bytearray()
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                if data:
                    raise RuntimeError("truncated Docker exec frame")
                raise EOFError("docker exec stream closed mid-frame")
            data.extend(chunk)
        return bytes(data)

    def recv(self, n=32768):
        if n <= 0:
            raise ValueError("recv size must be positive")
        while not self._pending:
            try:
                header = self._read_exact(8)
            except EOFError:
                return b""
            stream_type = header[0]
            size = int.from_bytes(header[4:], "big")
            if header[1:4] != b"\x00\x00\x00" or size > 1048576 or stream_type not in (1, 2):
                raise RuntimeError("invalid Docker exec frame")
            payload = self._read_exact(size)
            if stream_type == 1:
                self._pending.extend(payload)
            # stderr is not SSH data; never forward it to the client.
        result = bytes(self._pending[:n])
        del self._pending[:n]
        return result

    def sendall(self, data: bytes):
        self.sock.sendall(data)

    def shutdown_write(self):
        try:
            import socket as _s
            self.sock.shutdown(_s.SHUT_WR)
        except Exception:
            pass

    def close(self):
        try:
            if self.sock is not None:
                import socket as _s
                self.sock.shutdown(_s.SHUT_RDWR)
                self.sock.close()
        except Exception:
            pass
        try:
            self._stream.close()
        except Exception:
            pass


def generate_token_hex(n=16) -> str:
    return secrets.token_hex(n)
