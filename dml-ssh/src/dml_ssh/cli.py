"""dml-ssh local CLI (plan.md §5). Linux/macOS/Windows + system OpenSSH."""
import argparse
import getpass
import json
import os
import shlex
import stat
import subprocess
import sys
from pathlib import Path

from . import client as _client
from . import sshconfig as _sshconfig
from . import store as _store


def _scheduler(args):
    base = getattr(args, "scheduler", None) or os.getenv("DML_SCHEDULER_URL", "")
    if not base.startswith("https://"):
        print("error: --scheduler must be https://...", file=sys.stderr)
        sys.exit(2)
    return base.rstrip("/")


def cmd_login(args):
    base = _scheduler(args)
    username = args.username or input("username: ")
    password = args.password or getpass.getpass("password: ")
    data = _client.login(base, username, password)
    _store.save(base, data["access_token"], data["refresh_token"])
    print("logged in (interactive:ssh scope)")


def cmd_logout(args):
    state = _store.load()
    if state:
        try:
            _client.logout(state["scheduler"], state["refresh_token"])
        except Exception as exc:
            print(f"logout warning: {exc}", file=sys.stderr)
    _store.clear()
    print("logged out")


def cmd_configure(args):
    base = _scheduler(args)
    ident = args.runtime
    access = _client.ensure_access(base)
    info = _client.ssh_info(base, access, ident)
    if not info.get("ssh_capable"):
        print("SSH unavailable for this runtime (rebuild from an SSH-capable revision)", file=sys.stderr)
        sys.exit(1)
    if not info.get("ssh_ready"):
        print(f"SSH not ready ({info.get('ssh_status')}); retry after READY", file=sys.stderr)
        sys.exit(1)
    key_path = Path(args.identity or str(Path.home() / ".ssh" / f"dml-{info['runtime_id']}"))
    pub_path = _sshconfig.ensure_key(key_path)
    pubkey = pub_path.read_text().strip().splitlines()[0]
    host_key = info.get("host_key") or ""
    if not host_key.startswith("ssh-ed25519 "):
        print("invalid host key from server", file=sys.stderr)
        sys.exit(1)
    alias = f"dml-{info['runtime_id']}-g{info.get('ssh_generation', info['generation'])}"
    out = Path(args.output or str(Path.home() / ".ssh" / "dml-config"))
    snippet = _sshconfig.snippet(alias, info, key_path, pub_path, base)
    _sshconfig.write_snippet(out, alias, snippet)
    _sshconfig.write_known_host(info, host_key)
    print(f"configured host {alias} -> /workspace")
    print(f"Include file: {out}")
    print(f"Add to ~/.ssh/config if needed:\nInclude {out}")
    print("VS Code: Remote-SSH: Connect to Host -> %s, then open /workspace" % alias)
    _ = pubkey


def cmd_proxy(args):
    # ProxyCommand: stdin/stdout are binary SSH bytes ONLY. Errors -> stderr.
    import struct
    base = os.getenv("DML_SCHEDULER_URL", "") or _store.load_scheduler() or ""
    if not base.startswith("https://"):
        print("dml-ssh proxy: missing scheduler (configure first)", file=sys.stderr)
        sys.exit(1)
    try:
        access = _client.ensure_access(base)
        grant = _client.ssh_grant(base, access, args.runtime)
        pubkey = Path(args.public_key).read_text().strip().splitlines()[0]
        _client.proxy_stream(grant, pubkey, int(args.generation), sys.stdin.buffer, sys.stdout.buffer)
    except BrokenPipeError:
        pass
    except Exception as exc:
        print(f"dml-ssh proxy: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_doctor(args):
    base = _scheduler(args)
    ok = True
    for prog in ("ssh",):
        try:
            out = subprocess.run([prog, "-V"], capture_output=True, text=True, timeout=10)
            print(f"{prog}: {(out.stderr or out.stdout).strip()[:120]}")
        except Exception as exc:
            print(f"{prog}: MISSING ({exc})", file=sys.stderr)
            ok = False
    state = _store.load()
    print(f"credentials: {'present' if state else 'missing (run dml-ssh login)'}")
    if not ok:
        sys.exit(1)


def build():
    p = argparse.ArgumentParser(prog="dml-ssh")
    p.add_argument("--scheduler", default=os.getenv("DML_SCHEDULER_URL", ""))
    sub = p.add_subparsers(dest="cmd", required=True)
    # --scheduler is global (before subcommand), but the UI paste form puts
    # it after: `dml-ssh configure <id> --scheduler https://...`. Accept both
    # by repeating the flag on subcommands that need it.
    l = sub.add_parser("login")
    l.add_argument("--username", default="")
    l.add_argument("--password", default="")
    l.add_argument("--scheduler", default=argparse.SUPPRESS)
    l.set_defaults(func=cmd_login)
    o = sub.add_parser("logout")
    o.set_defaults(func=cmd_logout)
    c = sub.add_parser("configure")
    c.add_argument("runtime")
    c.add_argument("--identity", default="")
    c.add_argument("--output", default="")
    c.add_argument("--scheduler", default=argparse.SUPPRESS)
    c.set_defaults(func=cmd_configure)
    pr = sub.add_parser("proxy")
    pr.add_argument("--runtime", required=True)
    pr.add_argument("--generation", required=True)
    pr.add_argument("--public-key", required=True)
    pr.set_defaults(func=cmd_proxy)
    d = sub.add_parser("doctor")
    d.add_argument("--scheduler", default=argparse.SUPPRESS)
    d.set_defaults(func=cmd_doctor)
    return p


def main(argv=None):
    args = build().parse_args(argv)
    args.func(args)
