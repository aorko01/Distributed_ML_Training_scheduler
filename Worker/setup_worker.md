# Joining a new GPU worker to the Scheduler

Run these on the **new worker machine**. You only need this repo checkout and
`sudo`. The single installer provisions both the persistent systemd Worker and
the separate Electron monitoring console.

## 1. Prerequisites

- **Ubuntu** with the proprietary NVIDIA driver installed (reboot after
  installing it). The installer refuses non-Ubuntu hosts.
- Root + systemd (the worker runs as a root systemd unit).
- Docker storage that supports quotas — XFS with `pquota` is the safe
  choice. A default ext4 root fails the quota preflight, and the scheduler
  will never place interactive work on the host (silent, by design).
- Network egress to the scheduler (`https://`), the registry, and Headscale.
- A checkout of this repo (needs `Worker/`, `Access_Container/`,
  `deploy/interactive/worker/`).

## 2. Install Worker + Electron UI

```bash
cd <checkout>
sudo bash install.sh
```

On a fresh checkout, `install.sh` creates `Worker/.env` with the current
deployment's non-secret defaults. If `Worker/.env` already exists, it is never
overwritten. The installer automatically detects:

- Docker's real data root (`docker info`), even when it is not `/var/lib/docker`.
- Hostname/IP, CPU, RAM, disks, architecture, NVIDIA GPUs, and runtime support.
- A persistent Worker UUID and service secret.

The only secret it may ask for is the registry pull token. It first attempts to
import the existing Docker login for the invoking desktop user. A private CA
path cannot be inferred; the current public HTTPS deployment does not require
one. Custom deployments must set the relevant `*_CA_FILE` values themselves.

The NVIDIA host driver also cannot be selected safely without knowing the GPU
and kernel. Install a supported driver and reboot first; Docker and the NVIDIA
Container Toolkit are then configured automatically.

## 3. Register on the scheduler (manual step)

The installer prints a JSON entry like:

```json
{"<worker-uuid>": ["<worker-secret>"]}
```

There is no self-registration: merge that entry into
`/etc/dml/worker-credentials.json` **on the scheduler VM**, then recreate or
restart the scheduler API so it reloads the file. Back on the worker, type
`REGISTERED` to start.

Need the entry again later? Run:

```bash
sudo bash Worker/join_worker.sh --show-registration
```

## 4. Launch the UI

```bash
dml-worker-ui
```

You can also use the **DML Worker Console** desktop-menu entry. The UI is not a
systemd unit: closing it does not stop `dml-worker.service`.

## 5. Verify

```bash
systemctl is-active dml-worker
curl -s http://127.0.0.1:8600/api/status   # want connected:true, fresh lastHeartbeatAt
```

After any later `.env` change, just rerun the installer — it re-stages the
live config (`/etc/dml/worker.env`, the only file the service reads) and
restarts the worker:

```bash
sudo bash Worker/join_worker.sh --registered
```

> **Never restart while a runtime is live.** A restart kills active
> assignments mid-flight (their containers are cleaned, assignments
> released, clients 409). Drain/stop runtimes first, or accept the outage.
> Failed runtimes are kept for debugging by default (stopped/running
> containers stay until removed); set `INTERACTIVE_CLEANUP_ON_FAILURE=1`
> only if you want automatic removal. Failure bundles live under
> `/var/lib/dml-worker/interactive-failures/<assignment_id>/` (root-owned).

## 6. VS Code Remote-SSH support (live — flags are on)

SSH is deployed and passing traffic. The SSH transport
(`SSH_OPEN`/`SSH_READY` framing, raw byte relay, isolated SSH capacity) lives
in `Access_Container/interactive_access/`, which ships inside the pinned
`INTERACTIVE_ACCESS_IMAGE` (`access:runtime-ssh-v1`, digest in §2). The
scheduler control plane, gateway CLI flag, and this worker's
`INTERACTIVE_ALLOW_SSH=1` are all enabled; new SSH-capable revisions
(`io.dml.vscode-ssh-profile=v1`) serve VS Code sessions, old images stay
browser-capable with an actionable SSH-unavailable message.

The old tag `aorko123/access-sshd:latest` (Aug 2026, pre-SSH) must NOT be used.

### 6.1. Update the checkout

```bash
cd <checkout>
git fetch origin && git checkout main
git log --oneline -1   # want d69a5958 or newer
git status --short     # want clean
```

### 6.2. Build, verify, push the new Access image

```bash
cd <checkout>
uname -m   # want x86_64; the base digest is a manifest list, so a plain
           # build automatically targets this host's arch (linux/amd64)
docker build -t docker.io/aorko123/access:runtime-ssh-v1 Access_Container
# Verify the SSH transport is inside WITHOUT running it (create+cp only):
CID=$(docker create docker.io/aorko123/access:runtime-ssh-v1)
docker cp $CID:/service/interactive_access/protocol.py /tmp/acc-proto.py
docker cp $CID:/service/interactive_access/session.py /tmp/acc-sess.py
docker cp $CID:/service/interactive_access/config.py /tmp/acc-cfg.py
docker rm $CID
grep -c "SSH_OPEN" /tmp/acc-proto.py      # want >= 1
grep -c "run_ssh_session" /tmp/acc-sess.py # want >= 1
grep -c "ssh_capacity" /tmp/acc-cfg.py     # want >= 1
docker image inspect docker.io/aorko123/access:runtime-ssh-v1 \
  --format '{{.Os}}/{{.Architecture}} {{.Config.User}}'  # want linux/amd64 10001:10001
# Push with the registry credentials (Docker Hub login for aorko123):
docker push docker.io/aorko123/access:runtime-ssh-v1
docker image inspect docker.io/aorko123/access:runtime-ssh-v1 \
  --format '{{json .RepoDigests}}'   # record the @sha256 digest below
```

### 6.3. Update `Worker/.env`

In `<checkout>/Worker/.env` (the installer copies it to `/etc/dml/worker.env`):

```dotenv
INTERACTIVE_ACCESS_IMAGE=docker.io/aorko123/access@sha256:<PASTE-DIGEST-FROM-6.2>
INTERACTIVE_ALLOW_SSH=1
INTERACTIVE_SSH_MAX_DURATION_SECONDS=14400
INTERACTIVE_SSH_CAPACITY=8
```

Rules the installer enforces (`--check` fails otherwise): the digest must be
an immutable `@sha256:…` reference inside `INTERACTIVE_REGISTRY_PREFIXES`,
with no `REPLACE_WITH…` placeholders. `INTERACTIVE_ALLOW_SSH=1` is the
steady state now that the §6.5 gate has passed. Leave
`INTERACTIVE_ALLOW_DEVELOPER_MODE` / `INTERACTIVE_ALLOW_INTERNET` as they are;
the worker re-checks its own gates and fails closed on mismatch. No change to
`join_worker.sh` is needed: it already validates the digest, pre-pulls the
pinned images, and passes the SSH capacity into the Access container.

### 6.4. Validate and reinstall

```bash
cd <checkout>
bash Worker/join_worker.sh --check
sudo bash Worker/join_worker.sh --registered
sudo bash Worker/join_worker.sh --show-runtime-env
sudo journalctl -u dml-worker --since '-5 minutes' --no-pager | tail -20
curl -s http://127.0.0.1:8600/api/status   # want connected:true
```

The installer pulls the new access digest and restarts the worker. Existing
assignments are never adopted across the restart (by design — the worker
drains; do this when no live runtime is on this host, or accept the drain).

### 6.5. Verify the SSH path (real deployment gate, plan.md §8)

1. From the UI, start a runtime from an **SSH-capable revision**
   (`io.dml.vscode-ssh-profile=v1`) and wait for `READY` + the "Connect with
   VS Code" block.
2. On a client machine: `dml-ssh login`, `dml-ssh configure <runtime-id>`,
   then VS Code → Remote-SSH → `dml-<runtime>-g<generation>` → open `/workspace`.
3. Acceptance: `ssh <host> pwd` → `/workspace`; `id -u` → `10001`;
   `python -c "import torch; print(torch.cuda.is_available())"` as expected;
   `scp`/SFTP round-trip incl. a >2 MiB binary; two simultaneous SSH sessions
   plus the browser editor in parallel with mutually visible edits; VS Code
   port-forward to workload `127.0.0.1:8888` with no published Docker ports
   (`docker inspect` shows none, no host bind mounts).
4. Negative checks: stop the runtime and confirm sessions drop, grants revoke,
   and exact runtime containers are removed while unrelated objects remain.

### 6.6. Enablement order and rollback (done — kept for record)

Scheduler `INTERACTIVE_SSH_ENABLED=1`, gateway `GW_ALLOW_CLI=1`, and this
worker's `INTERACTIVE_ALLOW_SSH=1` are all live. New SSH-capable runtimes
only; old images stay browser-capable with an actionable SSH-unavailable
message.

Rollback: set `INTERACTIVE_ALLOW_SSH=0` here and rerun the installer (stops
new SSH relays; browser access keeps working), drain/stop SSH-capable
runtimes, then revert the access digest if needed. Never `docker prune`,
never reset Headscale, never touch another runtime's objects.

## 7. Ops cheat sheet (learned the hard way)

- **Journal speaks assignment IDs, not runtime IDs.** Container names,
  `dml.assignment` labels, and every worker log line use the assignment UUID.
  Map runtime → assignment first (`docker inspect … | grep dml.assignment`
  or the scheduler DB), then grep.
- **A 409 names its clause** (scheduler `_ssh_blocker()` detail):
  `state=…`, `health-stale`, `assignment-released`, `lease-or-generation`.
  `ssh-info` also returns `desired_state`/`state`/`failure_*` — a STOPPED
  runtime configures a dead host block, so check state before `configure`.
- **SSH auth checklist** (all seen live): image label
  `io.dml.vscode-ssh-profile=v1` present → `dml` account unlocked (`*`, never
  `!`) → `/run/dml-vscode-ssh/authorized_keys` is `600 dml:dml` (sshd opens it
  as `dml`; root-owned 600 gives `Permission denied`) → `/run/sshd` exists
  (fresh containers have an empty `/run` tmpfs) → single sshd listener on
  2222. Local proof without the client: temp keypair + `sshd -E` on port
  2223/2224, `ssh -BatchMode`, then delete the temp key.
- **Never restart with live runtimes** (§5). Two separate incidents killed
  healthy SSH sessions this way; the 409s afterwards are correct behavior,
  not a bug.
