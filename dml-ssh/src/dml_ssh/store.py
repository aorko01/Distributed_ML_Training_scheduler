"""Credential storage: OS keyring preferred, explicit user-only file fallback."""
import json
import os
import stat
from pathlib import Path

SCOPE = "interactive:ssh"


def _file() -> Path:
    return Path(os.getenv("DML_SSH_CREDENTIALS", str(Path.home() / ".dml-ssh-credentials.json")))


def save(scheduler: str, access: str, refresh: str):
    # Prefer OS credential store when available.
    try:
        import keyring  # type: ignore
        keyring.set_password("dml-ssh", scheduler + "|refresh", refresh)
        keyring.set_password("dml-ssh", scheduler + "|access", access)
        try:
            _file().unlink(missing_ok=True)
        except Exception:
            pass
        return
    except Exception:
        pass
    path = _file()
    if path.exists():
        st = path.stat()
        if st.st_mode & 0o077 or st.st_uid != os.getuid():
            raise RuntimeError("refusing to write bearer token: unsafe credential file")
    path.write_text(json.dumps({"scheduler": scheduler, "access_token": access, "refresh_token": refresh}))
    os.chmod(path, 0o600)
    try:
        os.chown(path, os.getuid(), os.getgid())
    except Exception:
        pass


def load():
    try:
        import keyring  # type: ignore
        sched = os.getenv("DML_SCHEDULER_URL", "")
        if sched:
            refresh = keyring.get_password("dml-ssh", sched.rstrip("/") + "|refresh")
            access = keyring.get_password("dml-ssh", sched.rstrip("/") + "|access")
            if refresh:
                return {"scheduler": sched.rstrip("/"), "access_token": access or "", "refresh_token": refresh}
    except Exception:
        pass
    path = _file()
    if not path.exists():
        return None
    st = path.stat()
    if st.st_mode & 0o077:
        raise RuntimeError("unsafe credential file permissions (must be 0600)")
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or "refresh_token" not in data:
        return None
    return data


def load_scheduler():
    data = load()
    return (data or {}).get("scheduler", "")


def update_access(access: str):
    data = load() or {}
    if not data:
        return
    try:
        import keyring  # type: ignore
        keyring.set_password("dml-ssh", data["scheduler"] + "|access", access)
        return
    except Exception:
        pass
    data["access_token"] = access
    _file().write_text(json.dumps(data))


def clear():
    try:
        import keyring  # type: ignore
        data = load() or {}
        sched = data.get("scheduler", os.getenv("DML_SCHEDULER_URL", ""))
        if sched:
            for suffix in ("|refresh", "|access"):
                try:
                    keyring.delete_password("dml-ssh", sched + suffix)
                except Exception:
                    pass
    except Exception:
        pass
    try:
        _file().unlink(missing_ok=True)
    except Exception:
        pass
