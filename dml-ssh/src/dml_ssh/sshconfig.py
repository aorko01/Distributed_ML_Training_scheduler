"""SSH config generation: pinned host keys, no AutoAddPolicy."""
import os
import shlex
import shutil
import stat
import subprocess
from pathlib import Path


def ensure_key(path: Path) -> Path:
    path = Path(path)
    pub = path.with_suffix(path.suffix + ".pub") if path.suffix else Path(str(path) + ".pub")
    if path.exists() and pub.exists():
        return pub
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(path)], check=True)
    os.chmod(path, 0o600)
    return pub


def snippet(alias, info, key_path, pub_path, scheduler):
    proxy = f"dml-ssh proxy --runtime {info['runtime_id']} --generation {info.get('ssh_generation', info['generation'])} --public-key {shlex.quote(str(pub_path))}"
    return "\n".join([
        f"Host {alias}",
        f"    HostName {alias}",
        "    User dml",
        f"    IdentityFile {key_path}",
        "    IdentitiesOnly yes",
        "    StrictHostKeyChecking yes",
        f"    ProxyCommand {proxy}",
        "",
    ])


def write_snippet(out: Path, alias, snippet_text):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = out.read_text() if out.exists() else ""
    # Replace same-host block idempotently; preserve unrelated entries.
    lines = existing.splitlines(keepends=True)
    kept, skip = [], False
    for line in lines:
        if line.strip().lower().startswith("host "):
            skip = alias in line.strip().split()[1:]
        if not skip:
            kept.append(line)
        elif not line.strip() or line.startswith((" ", "\t")):
            continue
    kept.append(snippet_text if snippet_text.endswith("\n") else snippet_text + "\n")
    tmp = out.with_suffix(".tmp")
    tmp.write_text("".join(kept))
    os.chmod(tmp, 0o600)
    tmp.replace(out)


def write_known_host(info, host_key):
    kh = Path.home() / ".ssh" / "known_hosts"
    try:
        alias = f"dml-{info['runtime_id']}-g{info.get('ssh_generation', info['generation'])}"
        existing = kh.read_text() if kh.exists() else ""
        lines = [l for l in existing.splitlines() if not l.startswith(alias + " ")]
        lines.append(f"{alias} {host_key.strip()}")
        kh.parent.mkdir(parents=True, exist_ok=True)
        kh.write_text("\n".join(lines) + "\n")
        os.chmod(kh, 0o600)
    except Exception:
        pass
