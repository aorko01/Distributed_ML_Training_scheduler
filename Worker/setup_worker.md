# Joining a new GPU worker to the Scheduler

Run these on the **new worker machine**. You only need this repo checkout and
`sudo`. The installer does everything else.

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

## 2. Fill in `Worker/.env`

```bash
cd <checkout>
cp Worker/.env.example Worker/.env
```

Edit `Worker/.env` — set these, leave the rest:

```dotenv
SCHEDULER_URL=https://scheduler.zulfiker.xyz
OBJECT_STORE_URL=https://object.zulfiker.xyz
INTERACTIVE_WORKER_ENABLED=1
INTERACTIVE_REGISTRY_PREFIXES=docker.io/aorko123
INTERACTIVE_ACCESS_IMAGE=docker.io/aorko123/access@sha256:5be114c0b1564ff18eece843bce01409e66275a6def674b4d1c9f420a455ec4e
INTERACTIVE_PREFLIGHT_IMAGE=docker.io/aorko123/quota-fixture@sha256:b0b2526e7fe571f62b3077ff32cf0371182348f400714bcc0c561bda70dd81c3
```

You do **not** need to set `DOCKER_DATA_ROOT` — the installer detects Docker's
real data root itself and writes it into the live config.

## 3. Run the installer

```bash
sudo bash Worker/join_worker.sh
```

This installs Docker + NVIDIA toolkit if missing, creates this worker's UUID
and secret, deploys everything under `/opt/dml`, installs the systemd units,
and pre-pulls the service images. You do **not** create the UUID/secret
yourself — the installer generates both.

## 4. Register on the scheduler (manual step)

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

## 6. VS Code Remote-SSH support (agent runbook for this worker host)

Yes — the Access container **must be rebuilt and pushed**: the SSH transport
(`SSH_OPEN`/`SSH_READY` framing, raw byte relay, isolated SSH capacity) lives
in `Access_Container/interactive_access/`, which ships inside the
`INTERACTIVE_ACCESS_IMAGE` this worker pulls by digest. A worker running the
old access digest accepts browser sessions fine but answers every `SSH_OPEN`
with an error. The control plane on the scheduler host is already live with
the SSH code and migrations; only the flags are off until the gate passes.

End to end, in order. Run everything below **on this worker host** (Ubuntu,
NVIDIA GPU, Docker + nvidia runtime). The old tag
`aorko123/access-sshd:latest` (Aug 2026, pre-SSH) must NOT be used.

### 6.1. Update the checkout

```bash
cd <checkout>
git fetch origin && git checkout <ssh-commit-or-main>
git log --oneline -1   # want 77fb4c6 ("added vs code remote support") or newer
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
INTERACTIVE_ALLOW_SSH=0
INTERACTIVE_SSH_MAX_DURATION_SECONDS=14400
INTERACTIVE_SSH_CAPACITY=8
```

Rules the installer enforces (`--check` fails otherwise): the digest must be
an immutable `@sha256:…` reference inside `INTERACTIVE_REGISTRY_PREFIXES`,
with no `REPLACE_WITH…` placeholders. Keep `INTERACTIVE_ALLOW_SSH=0` for now —
set it to `1` only at enablement (§6.6). Leave
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

### 6.6. Enablement order and rollback

Enable only after §6.5 passes: scheduler `INTERACTIVE_SSH_ENABLED=1` (+ API
restart) → gateway `GW_ALLOW_CLI=1` (+ restart) → this worker
`INTERACTIVE_ALLOW_SSH=1` + `sudo bash Worker/join_worker.sh --registered`.
New SSH-capable runtimes only; existing/browser runtimes are unaffected, and
old images stay browser-capable with an actionable SSH-unavailable message.

Rollback: set `INTERACTIVE_ALLOW_SSH=0` here and rerun the installer (stops
new SSH relays; browser access keeps working), drain/stop SSH-capable
runtimes, then revert the access digest if needed. Never `docker prune`,
never reset Headscale, never touch another runtime's objects.
