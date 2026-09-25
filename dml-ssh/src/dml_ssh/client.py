"""HTTPS/WSS client with TLS validation, single silent refresh, stdout cleanliness."""
import json
import ssl
import sys
import urllib.request

from . import store as _store

OPEN_TIMEOUT = 5


def _req(base, method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        raise RuntimeError(f"request failed: {type(exc).__name__}") from None


def login(base, username, password):
    return _req(base, "POST", "/interactive/cli/login", {"username": username, "password": password})


def _refresh(base, refresh_token):
    return _req(base, "POST", "/interactive/cli/refresh", {"refresh_token": refresh_token})


def ensure_access(base):
    state = _store.load()
    if not state:
        raise RuntimeError("run dml-ssh login first")
    return state.get("access_token", "")


def _authed(base, method, path, token, body=None):
    import urllib.error
    try:
        return _req(base, method, path, body, token)
    except RuntimeError:
        # Refresh silently once; on invalid refresh exit with login instruction.
        state = _store.load() or {}
        try:
            pair = _refresh(base, state.get("refresh_token", ""))
        except Exception:
            raise RuntimeError("run dml-ssh login first") from None
        _store.save(base, pair["access_token"], pair["refresh_token"])
        return _req(base, method, path, body, pair["access_token"])


def ssh_info(base, token, ident):
    try:
        return _req(base, "GET", f"/interactive/runtimes/{ident}/ssh-info", None, token)
    except RuntimeError:
        state = _store.load() or {}
        try:
            pair = _refresh(base, state.get("refresh_token", ""))
        except Exception:
            raise RuntimeError("run dml-ssh login first") from None
        _store.save(base, pair["access_token"], pair["refresh_token"])
        return _req(base, "GET", f"/interactive/runtimes/{ident}/ssh-info", None, pair["access_token"])


def ssh_grant(base, token, runtime_id):
    try:
        return _req(base, "POST", f"/interactive/runtimes/{runtime_id}/ssh-connection", {}, token)
    except RuntimeError:
        state = _store.load() or {}
        try:
            pair = _refresh(base, state.get("refresh_token", ""))
        except Exception:
            raise RuntimeError("run dml-ssh login first") from None
        _store.save(base, pair["access_token"], pair["refresh_token"])
        return _req(base, "POST", f"/interactive/runtimes/{runtime_id}/ssh-connection", {}, pair["access_token"])


def logout(base, refresh_token):
    try:
        return _req(base, "POST", "/interactive/cli/logout", {"refresh_token": refresh_token})
    except Exception:
        return {"status": "logged out"}


def proxy_stream(grant, public_key, generation, stdin, stdout):
    """WSS authenticate (5s) -> SSH_OPEN -> SSH_READY -> binary copy."""
    import struct
    import websocket  # type: ignore
    url = grant["wss_url"]
    ticket = grant["ticket"]
    ws = websocket.create_connection(url, timeout=10)
    try:
        ws.settimeout(OPEN_TIMEOUT)
        ws.send(json.dumps({"type": "authenticate", "ticket": ticket}))
        msg = ws.recv()
        try:
            hello = json.loads(msg) if isinstance(msg, str) else None
        except Exception:
            hello = None
        if not isinstance(hello, dict) or hello.get("type") != "ready":
            raise RuntimeError("gateway rejected ticket")
        # SSH_OPEN control record: versioned JSON, bounded, single key.
        opening = json.dumps({"version": 1, "public_key": public_key.strip(), "generation": generation},
                             separators=(",", ":")).encode()
        ws.send_binary(struct.pack("!BBI", 1, 48, len(opening)) + opening)
        ws.settimeout(10)
        raw = ws.recv()
        if isinstance(raw, str) or len(raw) < 6:
            raise RuntimeError("SSH rejected")
        ver, kind, ln = struct.unpack("!BBI", raw[:6])
        if ver != 1 or kind != 49 or len(raw) != 6 + ln:
            raise RuntimeError("SSH rejected")
        # Raw relay: WSS binary frames are arbitrary chunks. stdout stays
        # byte-clean (errors only to stderr).
        import threading
        stop = threading.Event()

        def pump_in():
            try:
                while not stop.is_set():
                    chunk = stdin.read(32768)
                    if not chunk:
                        stop.set()
                        return
                    ws.send_binary(chunk)
            except Exception as exc:
                print(f"dml-ssh proxy: {exc}", file=sys.stderr)
                stop.set()

        t = threading.Thread(target=pump_in, daemon=True)
        t.start()
        try:
            while not stop.is_set():
                ws.settimeout(60)
                try:
                    data = ws.recv()
                except Exception:
                    break
                if isinstance(data, str):
                    continue
                if not data:
                    break
                stdout.write(data)
                stdout.flush()
        finally:
            stop.set()
            t.join(timeout=2)
    finally:
        try:
            ws.close()
        except Exception:
            pass
