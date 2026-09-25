"""HTTPS/WSS client with TLS validation, single silent refresh, stdout cleanliness."""
import json
import ssl
import sys
import urllib.request

from . import store as _store

OPEN_TIMEOUT = 5


def _req(base, method, path, body=None, token=None):
    import urllib.error
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        # Surface status + server detail (409/429/503/401 are otherwise
        # indistinguishable as "request failed: HTTPError"). Body is bounded;
        # auth materials never appear in error details.
        try:
            raw = exc.read(4096).decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        detail = raw.strip()
        try:
            parsed = json.loads(raw) if raw else None
            if isinstance(parsed, dict) and isinstance(parsed.get("detail"), str):
                detail = parsed["detail"]
        except Exception:
            pass
        err = RuntimeError(f"request failed: HTTP {exc.code} {detail}".strip())
        err.status = exc.code  # type: ignore[attr-defined]
        err.detail = detail  # type: ignore[attr-defined]
        raise err from None
    except Exception as exc:
        err = RuntimeError(f"request failed: {type(exc).__name__}")
        err.status = None  # type: ignore[attr-defined]
        raise err from None


def _should_refresh(exc) -> bool:
    """Refresh only on 401/network errors; 409/429/503 must surface as-is."""
    return getattr(exc, "status", None) in (None, 401)


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
    except RuntimeError as exc:
        if not _should_refresh(exc):
            raise
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
    except RuntimeError as exc:
        if not _should_refresh(exc):
            raise
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
    except RuntimeError as exc:
        if not _should_refresh(exc):
            raise
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
        import os
        import threading
        stop = threading.Event()
        close_cause = {"msg": "gateway closed connection"}

        def pump_in():
            # Select-wakeup loop on POSIX so the thread always observes
            # stop and exits before interpreter teardown: a daemon thread
            # blocked in stdin.read races shutdown with
            # "Fatal Python error: _enter_buffered_busy". os.read on the
            # fileno avoids buffered-reader hidden bytes. Windows (no
            # select-on-pipe) keeps the blocking read fallback.
            try:
                fileno = stdin.fileno()
            except Exception:
                return
            use_select = True
            try:
                import select as _select
                _select.select([fileno], [], [], 0)
            except Exception:
                use_select = False
            try:
                while not stop.is_set():
                    if use_select:
                        import select as _select
                        try:
                            ready, _, _ = _select.select([fileno], [], [], 1.0)
                        except Exception:
                            return
                        if not ready:
                            continue
                        try:
                            chunk = os.read(fileno, 32768)
                        except OSError:
                            return
                    else:
                        try:
                            chunk = stdin.read(32768)
                        except (ValueError, OSError):
                            return  # stdio torn down; quiet exit
                    if not chunk:
                        stop.set()
                        return
                    if isinstance(chunk, str):
                        chunk = chunk.encode()
                    try:
                        ws.send_binary(chunk)
                    except Exception as exc:
                        close_cause["msg"] = f"send failed: {exc}"
                        stop.set()
                        return
            except Exception as exc:
                print(f"dml-ssh proxy: {exc}", file=sys.stderr)
                stop.set()

        t = threading.Thread(target=pump_in, daemon=False)
        t.start()
        try:
            while not stop.is_set():
                ws.settimeout(60)
                try:
                    data = ws.recv()
                except Exception as exc:
                    close_cause["msg"] = f"recv failed: {exc}"
                    break
                if isinstance(data, str):
                    continue
                if not data:
                    break
                stdout.write(data)
                stdout.flush()
        finally:
            stop.set()
            t.join(timeout=5)
            if t.is_alive():
                print("dml-ssh proxy: stdin pump did not exit cleanly",
                      file=sys.stderr)
            else:
                print(f"dml-ssh proxy: {close_cause['msg']}", file=sys.stderr)
    finally:
        try:
            ws.close()
        except Exception:
            pass
