import httpx
import time
from pathlib import Path
from .docker_ops import RuntimeFailure


class Endpoint:
    def __init__(self, socket, authority):
        self.client = httpx.Client(
            transport=httpx.HTTPTransport(uds=str(socket)),
            base_url="http://local-tailscaled.sock/localapi/v0/",
            timeout=3,
        )
        self.authority = authority
        self.socket = Path(socket)

    def call(self, method, path, **kwargs):
        self.authority()
        response = self.client.request(method, path, **kwargs)
        response.raise_for_status()
        return response

    def join(self, bootstrap):
        if bootstrap.get("already_enrolled"):
            if self.call("GET", "status").json().get("BackendState") != "Running":
                raise RuntimeFailure("START_FAILED")
            return
        # Key exists transiently in this process and an ephemeral protected tmpfs
        # file only, never environment, arguments, or ordinary journal columns.
        key_file = self.socket.parent / "enrollment.key"
        key_file.write_text(bootstrap["key"])
        key_file.chmod(0o600)
        try:
            self.call(
                "POST",
                "start",
                json={
                    "AuthKey": bootstrap["key"],
                    "UpdatePrefs": {
                        "ControlURL": bootstrap["login_server"],
                        "Hostname": bootstrap["hostname"],
                        "WantRunning": True,
                        "CorpDNS": False,
                    },
                },
            )
            self.call("POST", "login-interactive")
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if self.call("GET", "status").json().get("BackendState") == "Running":
                    return
                time.sleep(0.2)
            raise RuntimeFailure("START_FAILED")
        finally:
            key_file.unlink(missing_ok=True)
            bootstrap.pop("key", None)

    def serve(self, enabled):
        config = {"TCP": {"9000": {"TCPForward": "127.0.0.1:9000"}}} if enabled else {}
        if enabled:
            self.call("POST", "serve-config", json=config)
        else:
            # Withdrawing a fixed local listener is permitted after lease loss.
            result = self.client.post("serve-config", json=config)
            result.raise_for_status()

    def health(self):
        return self.call("GET", "status").json().get("BackendState") == "Running"

    def close(self):
        self.client.close()
