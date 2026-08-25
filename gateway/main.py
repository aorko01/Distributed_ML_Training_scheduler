import logging
import os
import subprocess
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import config
from api import router

logger = logging.getLogger("main")


def _wait_for_tailscaled_socket(timeout: float = 30.0):
    sock = "/var/run/tailscale/tailscaled.sock"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(sock):
            return
        time.sleep(0.5)
    raise RuntimeError("tailscaled socket did not appear in time")


def start_tailscale():
    """Start tailscaled and join the tailnet with the gateway's own pre-auth key."""
    os.makedirs("/var/run/tailscale", exist_ok=True)
    os.makedirs("/var/lib/tailscale", exist_ok=True)

    subprocess.Popen(
        [
            "tailscaled",
            "--state=/var/lib/tailscale/tailscaled.state",
            "--socket=/var/run/tailscale/tailscaled.sock",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    _wait_for_tailscaled_socket()

    if not config.TAILSCALE_AUTH_KEY or not config.HEADSCALE_URL:
        logger.warning(
            "TAILSCALE_AUTH_KEY or HEADSCALE_URL not set; "
            "gateway will not join the tailnet."
        )
        return

    subprocess.run(
        [
            "tailscale",
            "--socket=/var/run/tailscale/tailscaled.sock",
            "up",
            f"--login-server={config.HEADSCALE_URL}",
            f"--authkey={config.TAILSCALE_AUTH_KEY}",
            f"--hostname={config.GATEWAY_HOSTNAME}",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    logger.info("Gateway joined the tailnet as %s", config.GATEWAY_HOSTNAME)


def stop_tailscale():
    try:
        subprocess.run(
            [
                "tailscale",
                "--socket=/var/run/tailscale/tailscaled.sock",
                "down",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception as e:
        logger.warning("Failed to bring tailscale down: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(config.SSH_KEY_DIR, exist_ok=True)
    try:
        start_tailscale()
    except Exception as e:
        # Do not crash the API; SSH reachability just won't work until fixed.
        logger.error("Failed to join tailnet: %s", e)
    yield
    stop_tailscale()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(title="SSH Gateway Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=config.GATEWAY_API_PORT)
