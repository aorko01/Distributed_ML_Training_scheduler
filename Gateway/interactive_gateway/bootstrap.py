"""One-off restricted helper. Auth keys go through private LocalAPI, never argv."""
import asyncio
import json
import os
from pathlib import Path
import time
from uuid import uuid4

from .config import Settings, secret
from .management_client import ManagementClient, ManagementError
from .tailscale_api import TailscaleAPI


async def bootstrap(settings, management, restricted, local):
    path = Path(settings.enrollment_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = json.loads(path.read_text()) if path.exists() else {}
    deadline = time.monotonic() + 90
    status = await local.status()
    if status.get("BackendState") != "Running":
        # Durable request ID makes process interruption before response safe.
        if not state.get("idempotency"):
            state = {"idempotency": str(uuid4())}
            path.write_text(json.dumps(state))
            path.chmod(0o644)
        response = await restricted.request("POST", "enrollments", {"role": "gateway", "identity": settings.gateway_id,
            "generation": settings.generation}, headers={"Idempotency-Key": state["idempotency"]}, retry=True)
        state["enrollment_id"] = response["enrollment_id"]
        path.write_text(json.dumps(state))
        path.chmod(0o644)
        await local.start(response["login_server"], response["hostname"], response["key"])
    if "enrollment_id" not in state:
        raise RuntimeError("running sidecar has no managed enrollment metadata; explicit recovery required")
    last_state = None
    while time.monotonic() < deadline:
        try:
            backend = (await local.status()).get("BackendState")
            if backend != last_state:
                print("Gateway bootstrap backend=" + str(backend), flush=True)
                last_state = backend
            if backend == "Running":
                response = await management.request("POST", "enrollments/" + state["enrollment_id"] + "/confirm",
                    {"generation": settings.generation})
                if response["state"] == "CONFIRMED":
                    print("Gateway enrollment confirmed; persistent identity retained")
                    return
        except ManagementError as error:
            if error.status not in (409, 503):
                raise
        await asyncio.sleep(0.25)
    raise RuntimeError("gateway bootstrap readiness deadline exceeded")


async def main():
    settings = Settings.from_env()
    management = ManagementClient(settings)
    restricted = ManagementClient(settings, secret=secret("HM_BOOTSTRAP_SECRET"))
    local = TailscaleAPI(settings.socket)
    try:
        async with asyncio.timeout(100):
            await bootstrap(settings, management, restricted, local)
    finally:
        await management.close()
        await restricted.close()
        await local.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        print("Gateway bootstrap error=" + type(error).__name__, flush=True)
        # HTTP/LocalAPI exception repr must never leak a secret request.
        print("Gateway bootstrap failed; persistent state retained", flush=True)
        raise SystemExit(1)
