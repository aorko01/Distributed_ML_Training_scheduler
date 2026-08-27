# Shared access/SSH image for interactive sessions.
#
# This image is built and pushed to Docker Hub ONCE by the operator, then
# pulled by the worker per interactive session. It contains sshd + tailscaled
# and routes the user's shell into the env container via nsenter (shared PID
# namespace).
#
# Build & push commands (run from the repo root):
#   docker build -t aorko123/access-sshd:latest ./AccessContainer
#   docker push aorko123/access-sshd:latest
#
# The worker pulls aorko123/access-sshd:latest per session.
FROM debian:bookworm-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        openssh-server \
        curl \
        ca-certificates \
        bash \
        util-linux \
    && rm -rf /var/lib/apt/lists/*

# Install Tailscale (for tailnet connectivity).
RUN curl -fsSL https://tailscale.com/install.sh | sh

# Create the sandbox user that sshd will authenticate.
RUN id -u sandbox >/dev/null 2>&1 || useradd --create-home --shell /bin/bash sandbox

RUN mkdir -p /run/sshd /etc/ssh/sshd_config.d /var/run/tailscale /var/lib/tailscale

COPY access-entrypoint.sh /usr/local/bin/access-entrypoint.sh
RUN chmod +x /usr/local/bin/access-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/access-entrypoint.sh"]
