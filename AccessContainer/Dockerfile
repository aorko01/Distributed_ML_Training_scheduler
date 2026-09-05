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
#
# NOTE: this directory is the source of truth. Docker_Image_Builder/
# docker_ops.py (generate_access_dockerfile / generate_access_entrypoint)
# carries a reference copy kept in sync for tests; keep them aligned.
FROM debian:bookworm-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        openssh-server \
        curl \
        ca-certificates \
        bash \
        util-linux \
    && rm -rf /var/lib/apt/lists/*

# nsenter must run as root to enter the env container's namespaces (needs
# CAP_SYS_ADMIN). The SSH login user is the unprivileged `sandbox`, so make
# nsenter setuid-root. The entrypoint re-asserts this at runtime to survive a
# worker daemon with userns-remap (which remaps image-layer file ownership
# away from root and would otherwise silently break this setuid bit).
RUN chmod u+s /usr/bin/nsenter

# Install Tailscale (for tailnet connectivity).
RUN curl -fsSL https://tailscale.com/install.sh | sh

# Create the sandbox user that sshd will authenticate.
RUN id -u sandbox >/dev/null 2>&1 || useradd --create-home --shell /bin/bash sandbox

RUN mkdir -p /run/sshd /etc/ssh/sshd_config.d /var/run/tailscale /var/lib/tailscale

COPY access-entrypoint.sh /usr/local/bin/access-entrypoint.sh
RUN chmod +x /usr/local/bin/access-entrypoint.sh

# enter-env.sh is sshd's ForceCommand: it reconstructs the env container's
# real environment from /proc/1/environ (this container shares the env
# container's PID namespace) and nsenters into the env container's namespaces,
# giving the user a shell that behaves like `docker exec -it <env> /bin/bash`.
COPY enter-env.sh /usr/local/bin/enter-env.sh
RUN chmod +x /usr/local/bin/enter-env.sh

ENTRYPOINT ["/usr/local/bin/access-entrypoint.sh"]
