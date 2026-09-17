# Access Container 1.0

Generic Python standard-library terminal endpoint. The workload image, Tailscale
sidecar, and future Worker broker are separate components. This image contains
only `interactive_access`, never the test PTY broker or user project files.
See [terminal protocol and broker contract](../docs/terminal-stream-v1.md).

Build on a Docker-enabled Ubuntu runtime/test host:

```sh
docker build -t dml-access:1.0.0 Access_Container
```

Launch with numeric UID/GID 10001 (or an operator-selected numeric identity able
to read the mounted token/socket), read-only root, `/tmp` tmpfs, drop ALL
capabilities, no-new-privileges, PID/memory/CPU limits and the sidecar's network
namespace. Mount only that runtime's socket and token read-only. Do not mount the
Docker socket. Use `.env.example` paths with an opaque runtime ID. The default
listener is loopback 9000; health is loopback 9002. There are no published ports.
Pin a built image by digest when handing it to Worker. Use the exact pinned
Tailscale release in `test/interactive_e2e/compose.yaml` as a separate sidecar.

`/health/live` reports process liveness; `/health/ready` performs a bounded,
authenticated broker probe and returns only generic status. The terminal listener
opens only after readiness succeeds and closes on a failed periodic broker check. The future controller
must verify readiness before registration and withdraw Tailscale Serve/resource
readiness when this health check fails: a TCP-only probe of Tailscale Serve cannot
by itself certify backend readiness. Session admission always authenticates the
broker, and never falls back to a shell in this container.

Credential-free development checks (no Docker daemon/GPU/registry required):

```sh
python3 -m venv .venv
.venv/bin/pip install -r Access_Container/requirements-test.txt
(cd Access_Container && ../.venv/bin/python -m pytest -q)
```

The fake broker runs `/bin/sh` in a local PTY only inside test fixtures. It proves
framing, shell environment/cwd, resize, cancellation and reaping. Production
Docker exec, GPU access, host isolation, placement and snapshot saves are later
acceptance gates. No live Connect or Save is implemented in this phase.
