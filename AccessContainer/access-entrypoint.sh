#!/bin/bash
set -e

: "${HEADSCALE_URL:?HEADSCALE_URL is required}"
: "${HEADSCALE_AUTHKEY:?HEADSCALE_AUTHKEY is required}"
: "${SESSION_ID:?SESSION_ID is required}"
: "${SSH_PUBLIC_KEY:?SSH_PUBLIC_KEY is required}"

echo "Starting access container for session ${SESSION_ID}..."

# Start Tailscale daemon.
tailscaled \
    --state=/var/lib/tailscale/tailscaled.state \
    --socket=/var/run/tailscale/tailscaled.sock &

echo "Waiting for Tailscale daemon..."
until [ -S /var/run/tailscale/tailscaled.sock ]; do
    sleep 1
done

echo "Connecting to Headscale..."
tailscale up \
    --login-server="$HEADSCALE_URL" \
    --authkey="$HEADSCALE_AUTHKEY" \
    --hostname="$SESSION_ID"

echo "Tailscale connected."

# SSH setup: install the gateway's public key for the sandbox user.
mkdir -p /home/sandbox/.ssh
echo "$SSH_PUBLIC_KEY" > /home/sandbox/.ssh/authorized_keys
chown -R sandbox:sandbox /home/sandbox/.ssh
chmod 700 /home/sandbox/.ssh
chmod 600 /home/sandbox/.ssh/authorized_keys

# sshd config: force every connection to nsenter into the env container's
# PID 1 (the env container's `sleep infinity`), entering all its namespaces
# (mount, UTS, IPC, net, PID) so the user lands inside the training container.
mkdir -p /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/sandbox.conf <<'SSHD'
PasswordAuthentication no
PubkeyAuthentication yes
PermitRootLogin no
AllowUsers sandbox
ForceCommand nsenter -t 1 -m -u -i -n -p -- /bin/bash -l
SSHD

echo "Tailscale IP: $(tailscale ip -4 || true)"

echo "Starting SSH server..."
exec /usr/sbin/sshd -D -e
