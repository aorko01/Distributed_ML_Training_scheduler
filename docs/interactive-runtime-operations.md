# Interactive runtime operations

Runtime admission defaults to disabled. This phase adds Start, runtime status,
Stop and a short connection check. There is no editor, notebook, snapshot, commit
or Save implementation. Workloads are temporary Docker containers; Stop discards
their writable changes. Source revisions remain immutable and can be started
again explicitly. Browser verification disconnect does not stop the runtime.
`INTERACTIVE_LIFETIME_SECONDS=0` disables the optional maximum lifetime; positive
values publish a runtime deadline and stop it independently of browser activity.

## Host ownership and prerequisites

| Host | Processes and host-local paths |
| --- | --- |
| Scheduler/control plane | PostgreSQL, Scheduler, private management controller access; Worker secret map, controller secret, management CA under `/etc/dml` |
| Gateway/management | Existing `deploy/interactive/` deployment, Headscale administration/signing/encryption keys, public HTTPS/WSS reverse proxy |
| Ubuntu Worker/Docker | Host-installed Python Worker and lease guard; Docker daemon, NVIDIA runtime/GPU, `/var/lib/dml-worker`, `/run/dml-interactive/<assignment>`, `/run/dml-interactive-endpoints/<assignment>`, registry pull secret, control/Headscale CAs |
| Browser/UI host | UI built with its public Scheduler HTTPS URL; connection tickets remain in memory only |

Use reachable DNS/HTTPS control URLs across hosts, never `localhost`. Workers
need outbound Scheduler HTTPS and Headscale/tailnet transport; no public inbound
Worker exec port. The dashboard listens on `127.0.0.1:8600`. Only Access and the
Tailscale sidecar share a network namespace. The workload uses network `none`,
no host mounts or published ports, and only its assigned GPU UUID. Access has no
Docker socket, endpoint state/local API socket or service credentials.

The supported runtime host is Ubuntu with a host-installed root Worker under
systemd, Linux pidfds, Docker `overlay2` on XFS configured with project quotas,
and NVIDIA Container Toolkit/runtime. Docker's per-container `size` storage option
requires this storage configuration; see [Docker storage options](https://docs.docker.com/reference/cli/docker/container/run/#set-storage-driver-options-per-container).
Preflight creates/removes one exact tiny quota-fixture container and fails closed
when quota creation, root host-process inspection or NVIDIA runtime is unavailable.
It does not reformat disks, migrate Docker storage or prune images/volumes.
Do not run unmanaged training processes on Worker hosts. GPU process inventory,
configured baseline VRAM and utilization thresholds are rechecked before launch;
unknown inventory is ineligible. Baseline memory does not excuse a running GPU
process. All GPUs must be free even though the profile assigns only one GPU.

CPU/RAM/PID/writable-layer/log limits are independent; pulled image layers require
separate disk headroom. One interactive runtime exclusively reserves every GPU
and the entire machine through pull, startup, Stop, LOST and uncertain cleanup.
Estimation takes priority over eligible interactive work, then batch retries and
training. Active batch work is allowed to finish and blocks interactive Start
placement. Incompatible interactive requests do not block batch placement.

## Drain, backup and migrate on the control-plane host

1. Disable legacy claims before replacing the Scheduler, drain active legacy
   batch/estimation work, and reconcile every old Worker marker/container. New
   Scheduler legacy `/jobs/pull_job` and `/jobs/resume` routes return 410. Existing
   image-builder callbacks retain their separate build fences.
2. Back up the existing PostgreSQL database using the normal protected connection
   configuration. Keep the backup outside diagnostic artifacts.
3. Install upgraded Scheduler dependencies, copy `Scheduler/.env.runtime.example`
   to protected deployment configuration, and leave `WORKER_NEW_WORK_ENABLED=0`
   and `INTERACTIVE_RUNTIME_ENABLED=0`.
4. Apply the ordered migrations before admission. The runner serializes migrations
   under PostgreSQL advisory lock 764293810; migration 002 adds Worker metadata,
   runtime/assignment constraints, partial unique indexes and immutable tombstones.

```sh
cd /opt/dml/Scheduler
/opt/dml/venv/bin/pip install -r requirements.txt
# Export DATABASE_URL from protected service configuration, without printing it.
/opt/dml/venv/bin/python -c 'from app.db.database import run_migrations; run_migrations()'
```

`create_all` is only a development bootstrap; it never upgrades production
runtime tables. Existing installations retain their old batch tables. PostgreSQL
migration 001's immutable ready-revision trigger remains in effect.

Provision one distinct high-entropy Worker secret per Worker. On Scheduler,
`WORKER_CREDENTIALS_FILE` is a protected JSON map:
`{"<worker-uuid>":["<random-secret>"]}`. On that Worker only, put the same secret
in `WORKER_SERVICE_CREDENTIAL_FILE`. Use 0600 root-owned regular files; never
symlinks, default/example credentials or raw secrets in `.env`/command arguments.
To rotate, temporarily retain two active secrets in the Scheduler entry, update
that Worker file atomically, then remove the old secret. Removing an entry/secret
revokes it on the next authenticated request. Worker, builder, Gateway, controller
and registry credentials remain separate. Workers never receive controller or
Headscale administration secrets.

For Compose Scheduler, layer `compose.runtime.yaml` with the existing Compose
files and set the required absolute secret/CA paths on the Scheduler host. This
is an additive overlay, not a Worker deployment inside Scheduler Compose.

## Install and supervise on each Ubuntu Worker host

Publish the existing Access image and record its immutable registry digest:

```sh
cd /opt/dml
# Run on an authorized image publishing host using that host's credentials.
docker build -t registry.example.internal/team/access:runtime-v1 Access_Container
docker push registry.example.internal/team/access:runtime-v1
docker image inspect registry.example.internal/team/access:runtime-v1 --format '{{json .RepoDigests}}'
```

Do the same for `test/interactive_e2e/runtime/Dockerfile` as the tiny preflight
fixture. Use those recorded digests in Worker configuration, not placeholders or
mutable tags. The workload digest always comes from the Scheduler's ready revision.
Docker exec behavior follows the [Engine exec API](https://docs.docker.com/reference/api/engine/version/v1.40/#tag/Exec);
the broker creates a fixed `/bin/sh` PTY as the validated image User/WORKDIR, not
its image's training command. Empty/root User is rejected unless the fixed profile
explicitly sets `INTERACTIVE_ALLOW_ROOT=1`. HOME for broker exec is `/tmp`, TERM is
`xterm`; neither is a host bind mount. Unset WORKDIR uses `/workspace`; an image
must support and permit access to its chosen shell/workdir/keepalive utilities.
Image-declared volumes are rejected so future saves can refer to writable-layer
files without hidden anonymous mounts. No automatic image commit occurs.

```sh
cd /opt/dml
python3 -m venv venv
venv/bin/pip install -r Worker/requirements.txt
sudo install -d -m 0700 /etc/dml /var/lib/dml-worker
sudo install -m 0600 Worker/.env.example /etc/dml/worker.env
sudo install -m 0644 deploy/interactive/worker/dml-worker.service /etc/systemd/system/
sudo install -m 0644 deploy/interactive/worker/dml-worker-lease-guard.service /etc/systemd/system/
```

Edit `/etc/dml/worker.env` with this host's paths, reachable HTTPS URLs, pinned
Access/preflight digests and registry allowlist before startup. Set
`WORKER_ID_FILE=/var/lib/dml-worker/worker-id` to the provisioned Scheduler secret
map identity. If the Worker has no existing ID, provision a new UUID in that file
and bind its secret map entry before starting it. The state directory's SQLite
journal uses WAL/FULL synchronization and a host service lock; two Worker processes
cannot control the same host. Registry pull credentials, when needed, are a 0600
JSON file `{server,username,password}` containing pull-only permissions. Temporary
Docker auth configuration is created under `/run/dml-interactive-pulls`; it never
enters workload/container environment or arguments. TLS trusts use the host's CA
or configured CA files. Endpoint CA binds belong only to the sidecar.

Pre-pull the pinned Access, Tailscale and preflight images on the Worker host with
its pull-only registry configuration. Enable `INTERACTIVE_WORKER_ENABLED=1` once
host prerequisites have passed:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now dml-worker-lease-guard dml-worker
sudo systemctl status dml-worker dml-worker-lease-guard --no-pager
sudo journalctl -u dml-worker --since '-5 minutes' --no-pager
```

The independently supervised lease guard inspects the exact assignment journal
and Docker labels every second. It removes expired exact containers if Python's
execution/heartbeat loop stalls. SIGTERM stops admission before terminating
heartbeats. `ExecStopPost` removes exact journal-bound containers after abnormal
termination; startup reconciliation then removes secrets/sockets, sends old-instance
cleanup and waits for Scheduler release. Never adopt an old interactive runtime
across instance restart. SIGKILLed Python cannot run `finally`, so both guard and
startup/ExecStopPost cleanup are required. Nested-Docker Workers are not supported.

Heartbeat defaults to 5 seconds with a 45-second assignment lease. Dashboard
interval updates cannot exceed the renewal margin. Runtime pause suppresses claims
and resume scans, never heartbeat or Stop decisions. Endpoint/session leases are
separate management contracts. Worker renewals use monotonic request-send deadlines
with a five-second safety margin; old, delayed or expired replies cannot resurrect
execution. Unreachable Docker, corrupt/missing journals or uncertain launch tasks
quarantine the host; never advertise it idle or delete unrelated Docker objects.

## Enable and check the actual deployed path

Configure management private HTTPS/controller permissions, public Gateway WSS
origin/identity, existing Origin allowlist and tailnet policy. Keep the existing
Gateway probe/session lease behavior. The endpoint publishes only tailnet TCP
9000 to `127.0.0.1:9000` using the pinned [Tailscale Serve configuration](https://tailscale.com/docs/reference/tailscale-cli/serve).
Access health 9002 stays loopback-only. Keys join via the protected sidecar Unix
local API, are wiped after join and are never replayed after confirmed membership.
Scheduler independently confirms the exact issued key/node and observes management
READY/endpoint version. Workload/broker/Access health gates leases and grants.

Enable new Worker claims first, then interactive admission after smoke checks.
Start requires a current or explicitly selected owned IMAGE_READY revision and
an Idempotency-Key. The owner API exposes runtime states/safe failures, not Docker
IDs, management IDs, keys or host paths. Connect returns a no-store single-use
grant. Stop racing a remote grant or enrollment revokes the exact late side effect.
Connection succeeds only after Gateway ready, workload OPENED and bounded CLOSE/
EXIT cleanup. The workload stays running; ticket retry requires another explicit
connection request. Tickets never enter URLs/browser storage/logs/analytics.

On a separate provisioned Ubuntu Docker host, install runtime gate dependencies
and run the CPU broker fixture (does not prove GPU/resource quotas/NAT):

```sh
/opt/dml/venv/bin/pip install -r test/interactive_e2e/runtime/requirements.txt
# Build/push runtime/Dockerfile to a disposable registry; export its exact digest.
sudo --preserve-env=RUNTIME_FIXTURE_DIGEST /opt/dml/venv/bin/python test/interactive_e2e/runtime/check_broker.py
```

On the actual GPU Worker host, use a disposable owned ready image produced by
the existing builder, start the actual Worker/systemd services and control plane,
and run:

```sh
/opt/dml/venv/bin/python -m playwright install chromium
sudo /opt/dml/venv/bin/python test/interactive_e2e/runtime/check_deployment.py \
  --scheduler https://scheduler.example.internal \
  --workspace-id UUID --owner-token-file /etc/dml/acceptance-owner-token \
  --origin https://ui.example.net --ui-url https://ui.example.net
```

This gate uses actual Scheduler Start/status/grants/Stop, three labelled containers,
real NVIDIA UUID access, WSS OPENED/CLOSE repeated sessions, increasing heartbeats,
zero new claims while interactive holds, browser success and exact Stop cleanup.
Run Gateway/control plane separately from the Worker to verify public browser
access without Tailscale and actual cross-host routing. CPU broker and mocked
unit tests cannot establish production GPU, registry pull, quotas or NAT behavior.

Exercise the priority order with staged real estimation, interactive, retry and
training queues: estimation first; after cleanup, interactive; Stop releases the
host before retry/training. With active batch work, observe interactive stays queued
even if another GPU is free. An incompatible interactive profile must permit
fitting batch work. Preserve an unrelated Docker container/image/volume and
Headscale node while repeating Stop/start, Worker SIGKILL, held/hung launch,
Scheduler network partition, host restart, Docker restart, Access/broker failure,
endpoint offline, management outage and failed probe. Assert exact objects removed,
all PTY descendants exited, grants denied, reservations held until cleanup and
new generation Start required after failure. Record host versions, safe outcome
codes and counts only, not env/inspect/raw terminal bytes or credentials.

## Recover and roll back

LOST is not proof containers disappeared. Worker cleanup acknowledgment and exact
management authorization/node cleanup must both be confirmed before release.
Pending management revocation keeps the machine reserved. Never clear a hold by
TTL, missing Redis mapping, GPU model, utilization or deleting assignment rows.
For support, inspect protected journal and exact Docker labels locally, validate
identities and complete the assignment-bound cleanup endpoint. A missing journal
for a Scheduler-held assignment requires operator reconciliation; the Worker does
not guess ownership or broadly remove containers.

Rollback disables interactive starts, stops/drains all current runtimes, confirms
both local/management cleanup and batch reservations, then deploys older processes.
An older Worker/Scheduler must not manage live new-format assignments. Retain the
additive migration and assignment tombstones for stale-message fencing. No global
Docker prune, image deletion, volume deletion or Headscale reset is part of runtime
cleanup. Source image retention follows `interactive-images-operations.md`.
