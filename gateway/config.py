import os

# API listen port.
GATEWAY_API_PORT = int(os.getenv("GATEWAY_API_PORT", "8200"))

# Where generated SSH private keys are stored (volume-mounted).
SSH_KEY_DIR = os.getenv("SSH_KEY_DIR", "/data/ssh-keys")

# Gateway's own Headscale pre-auth key (long-lived reusable key provisioned
# out-of-band). Required for the gateway to join the tailnet on startup.
TAILSCALE_AUTH_KEY = os.getenv("TAILSCALE_AUTH_KEY", "")

# Headscale control server URL, e.g. https://headscale.example.com.
HEADSCALE_URL = os.getenv("HEADSCALE_URL", "")

# Hostname this node registers under in the tailnet.
GATEWAY_HOSTNAME = os.getenv("GATEWAY_HOSTNAME", "gateway")

# Scheduler API URL. Required for SSH auth: the gateway validates the
# user's one-time SSH password against the Scheduler's ephemeral-password
# verification endpoint (POST /interactive/sessions/verify-ephemeral).
SCHEDULER_API_URL = os.getenv("SCHEDULER_API_URL", "")

# SSH connection timeout in seconds.
SSH_CONNECT_TIMEOUT = int(os.getenv("SSH_CONNECT_TIMEOUT", "10"))

# SSH login user inside interactive containers.
SSH_USER = os.getenv("SSH_USER", "sandbox")

# SSH Server Bastion Config
GATEWAY_SSH_PORT = int(os.getenv("GATEWAY_SSH_PORT", "2222"))
GATEWAY_PUBLIC_HOST = os.getenv("GATEWAY_PUBLIC_HOST", "")
GATEWAY_HOST_KEY_PATH = os.getenv("GATEWAY_HOST_KEY_PATH", "/data/ssh-keys/gateway_host_key")
