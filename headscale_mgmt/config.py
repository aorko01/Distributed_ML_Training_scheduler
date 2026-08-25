import os

# Headscale access: either talk to a remote control plane over its HTTP API
# (HEADSCALE_API_URL + HEADSCALE_API_KEY) or shell out to the local headscale
# CLI installed in this container (default).
HEADSCALE_API_URL = os.getenv("HEADSCALE_API_URL", "").rstrip("/")
HEADSCALE_API_KEY = os.getenv("HEADSCALE_API_KEY", "")
HEADSCALE_CLI_PATH = os.getenv("HEADSCALE_CLI_PATH", "headscale")

# Tailnet user that pre-auth keys are created for.
HEADSCALE_USER = os.getenv("HEADSCALE_USER", "sandbox")

# Shared secret the Scheduler must present as `Authorization: Bearer <token>`.
AUTH_TOKEN = os.getenv("AUTH_TOKEN", "change-me")

# Default pre-auth key lifetime (seconds).
PREAUTH_KEY_EXPIRY = int(os.getenv("PREAUTH_KEY_EXPIRY", "3600"))

LISTEN_HOST = os.getenv("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.getenv("LISTEN_PORT", "8100"))
