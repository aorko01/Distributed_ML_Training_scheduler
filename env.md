# Runtime environment and credential placement

This repository is deployed across several machines.  Use routable HTTPS names
below; `localhost` inside a container refers to that container, not another
machine.  Keep secret files out of the repository and use root-owned regular
files with mode `0600` (directories `0700`) unless the deployment requires a
different group-readable mode.

## Deployment map: what must run on each machine

Copy this repository to every machine that runs one of its components, but put
that machine's environment files and secrets **only on that machine**. Do not
copy one machine's `.env`, credential files, or Headscale secrets to another.
All URLs below must be routable from the component using them; a container's
`localhost` is not another VM.

### Normal batch deployment

| Machine | Required local configuration | Start/update command | Additional requirement |
| --- | --- | --- | --- |
| Scheduler VM | `Scheduler/.env` | `cd Scheduler && docker compose up -d --build` | The API must reach the object-store URL. Keep the Postgres volume name stable. |
| Object-store VM | `Object_store/.env` containing non-default `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD` | `cd Object_store && docker compose up -d --build` | Create the `uploads` and `outputs` buckets once, and expose the S3/API URLs used by the other VMs. |
| Image-builder VM | `Docker_Image_Builder/.env` | `cd Docker_Image_Builder && docker compose up -d --build` | Docker Engine and `/var/run/docker.sock` must be available; it must reach Scheduler and object store. |
| GPU Worker VM | `/etc/dml/worker.env` (or an explicitly loaded equivalent), worker ID, and worker service-secret file | Start `Worker/main.py`, preferably with `dml-worker.service` | Docker, GPU drivers, Scheduler access, and object-store access are required. |

Before starting the Worker, create one worker identity set: put its UUID in
`/var/lib/dml-worker/worker-id`, put its matching secret in
`/etc/dml/worker-service.secret`, and add that UUID/secret pair to the
Scheduler VM's `/etc/dml/worker-credentials.json`. A Worker `.env` alone is
therefore not sufficient.

For a production Worker, install and enable
`deploy/interactive/worker/dml-worker.service`; it supplies the environment
from `/etc/dml/worker.env`, restarts the process after failure, and ensures it
is not lost when an SSH session closes. If starting `main.py` manually, load
the same variables into that process and keep it under a supervisor such as
systemd or tmux.

### Extra requirements for interactive browser access

The normal batch commands above do **not** enable interactive terminals or
workspaces. In addition to the normal deployment, all of the following must
be ready:

1. On the Scheduler VM, configure the runtime files and protected credentials
   described below, `/etc/distributed-ml/interactive.env`, and the interactive
   secret directory. Run the repository-root deployment command
   `sudo -n env REQUIRE_INTERACTIVE=1 bash restart.sh`; it updates Scheduler,
   management, Tailscale sidecar, bootstrap, and Gateway.
2. Keep the Headscale daemon installed and running separately. It is not
   started by either Scheduler Compose or `restart.sh`.
3. Publish the Scheduler API and Gateway through trusted HTTPS. The Gateway
   WebSocket origin (`INTERACTIVE_GATEWAY_WSS_ORIGIN`) must be reachable by
   users' browsers and must match `GW_ORIGINS`; configure the host reverse proxy
   to forward WebSocket upgrades to the Gateway port.
4. On the builder VM, use `Docker_Image_Builder/compose.interactive.yaml` with
   the normal Compose file and provision the matching interactive-builder
   secret. For example: `docker compose -f docker-compose.yml -f
   compose.interactive.yaml up -d --build`.
5. On every interactive Worker, set `INTERACTIVE_WORKER_ENABLED=1`, configure
   its allowed registry prefixes, pull-only registry credential file, and the
   pinned access/preflight image digests. Supply a Headscale CA file when its
   endpoint uses a private CA.
6. Build the User UI with its public Scheduler API URL. It receives no
   privileged interactive secrets; the browser connects to the published
   Gateway WSS URL using a ticket issued by the Scheduler.

Keep `WORKER_NEW_WORK_ENABLED=0` and `INTERACTIVE_RUNTIME_ENABLED=0` until the
worker, image builder, Gateway, Headscale connectivity, HTTPS proxy, and a
real browser WebSocket check have all passed. Enable each value only after its
corresponding acceptance checks succeed.

## 1. Scheduler, Gateway, and Headscale-management host

### Scheduler Compose

Create `Scheduler/.env` on this host.  It is consumed by
`Scheduler/docker-compose.yml` and must contain at least:

```dotenv
POSTGRES_USER=<database-user>
POSTGRES_PASSWORD=<strong-database-password>
POSTGRES_DB=app
POSTGRES_DATA_VOLUME=scheduler-postgres-data       # optional; keep stable
DB_PORT=5432                                       # optional host mapping
API_PORT=8000                                      # or the HTTPS proxy upstream port
JWT_SECRET_KEY=<long-random-JWT-secret>
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30
OBJECT_STORE_URL=https://objects.example.internal
OBJECT_STORE_BUCKET=uploads
OBJECT_OUTPUT_BUCKET=outputs
IMAGE_BUILDER_HEARTBEAT_TIMEOUT_SECONDS=45
WATCHDOG_SCAN_INTERVAL_SECONDS=5
```

`PGADMIN_*`, `REDIS_PORT`, and `CORS_ORIGINS` may appear in the existing file,
but the supplied Compose file currently hard-codes the pgAdmin defaults/port and
the application currently allows all CORS origins.  They are not effective
Compose controls as written.

For managed workers and interactive workspaces, also create
`Scheduler/.env.runtime` from `Scheduler/.env.runtime.example`.  Set:

```dotenv
WORKER_NEW_WORK_ENABLED=0                         # change to 1 only after worker acceptance
INTERACTIVE_RUNTIME_ENABLED=0                     # change to 1 only after interactive acceptance
WORKER_CREDENTIALS_FILE=/etc/dml/worker-credentials.json
WORKER_ASSIGNMENT_LEASE_SECONDS=45
SCHEDULING_POLICY=three-tier
INTERACTIVE_MANAGEMENT_URL=https://management.example.internal
INTERACTIVE_MANAGEMENT_CA_FILE=/etc/dml/management-ca.pem # omit for public CA
INTERACTIVE_CONTROLLER_SECRET_FILE=/etc/dml/controller.secret
INTERACTIVE_GATEWAY_WSS_ORIGIN=wss://gateway.example.internal
INTERACTIVE_GATEWAY_ID=gateway-main
INTERACTIVE_BUILDER_SECRET_FILE=/run/secrets/interactive-builder
```

The remaining `INTERACTIVE_*` sizing/profile settings in the example are
optional policy settings; retain them if the interactive runtime is enabled.
Start this overlay with `Scheduler/docker-compose.yml` plus
`Scheduler/compose.runtime.yaml` and `Scheduler/compose.interactive.yaml`.
The *host shell/deployment environment* additionally needs these absolute paths
for the volume mounts:

```dotenv
WORKER_CREDENTIALS_HOST_FILE=/etc/dml/worker-credentials.json
CONTROLLER_SECRET_HOST_FILE=/etc/dml/controller.secret
MANAGEMENT_CA_HOST_FILE=/etc/dml/management-ca.pem       # private CA only
INTERACTIVE_BUILDER_SECRET_HOST_FILE=/etc/dml/secrets/interactive-builder
```

Credential contents and recipients:

| File on Scheduler host | Contents | Also placed on |
| --- | --- | --- |
| `/etc/dml/worker-credentials.json` | JSON map `{"<worker-id>":["<worker-secret>"]}` | Each worker gets only its own matching secret, not this map. |
| `/etc/dml/controller.secret` | Controller-to-management shared secret | The management-only `controller` file in the interactive secret directory; never a gateway, worker, or builder. |
| `/etc/dml/management-ca.pem` | CA used to verify private management HTTPS | Scheduler only (and only when using a private CA). |
| `/etc/dml/secrets/interactive-builder` | Random builder service secret | Docker-image-builder host, same bytes. |

### Gateway and Headscale management Compose

The installed Headscale daemon is external; `deploy/interactive/compose.yaml`
starts the **management**, Tailscale sidecar, bootstrap, and gateway containers
on this machine.  Create `/etc/distributed-ml/interactive.env` from
`deploy/interactive/interactive.env.example`:

```dotenv
HM_HEADSCALE_URL=https://headscale-admin.example.internal
HM_LOGIN_SERVER=https://headscale.example.internal
INTERACTIVE_SECRET_DIR=/etc/distributed-ml/interactive-secrets
GATEWAY_ID=gateway-main
GATEWAY_GENERATION=gateway-v1
HM_SIGNING_KID=primary
GW_ORIGINS=https://ui.example.internal
GW_ALLOW_CLI=0
GATEWAY_PORT=8030
```

At `INTERACTIVE_SECRET_DIR`, provision these files: `headscale` (a Headscale
administrative API key), `controller`, `gateway`, `bootstrap`, `encryption`,
`signing.pem`, and `public.json`.  `controller` must match the Scheduler
controller-secret file.  `gateway` is only for gateway/management/bootstrap;
`bootstrap` is only mounted for bootstrap; private signing and encryption keys
are management-only.  The Headscale admin key is management-only.  Do not send
any of these to workers, UIs, or the image builder.

The Scheduler needs trusted network access to the management URL and the WSS
origin must be publicly reachable by browsers.  Headscale's ordinary server
configuration/API key management remains on the Headscale installation; it is
not configured through `Gateway/.env.example` or `Headscale_Management/.env.example`.

## 2. Docker image-builder host

Create `Docker_Image_Builder/.env` on the builder host (or inject the same
variables through its deployment system):

```dotenv
SCHEDULER_API_URL=https://scheduler.example.internal
OBJECT_STORE_URL=https://objects.example.internal
OBJECT_STORE_BUCKET=uploads
OBJECT_OUTPUT_BUCKET=outputs                       # optional, defaults to outputs
DOCKER_HUB_USERNAME=<builder-registry-user>
DOCKER_HUB_PASSWORD=<builder-registry-token>
IMAGE_BUILDER_ID=<stable-unique-builder-name>       # optional; hostname by default
POLL_INTERVAL=10
MAX_CONCURRENT_BUILDS=3
IMAGE_BUILDER_HEARTBEAT_INTERVAL=5
IMAGE_BUILDER_HEARTBEAT_TIMEOUT=45
DEBUG_SAVE_LOCAL=false
```

It also needs Docker Engine access and `/var/run/docker.sock`; no Scheduler
database, JWT secret, worker service secret, controller secret, or Headscale key
belongs here.  For interactive image builds, place the same random value as the
Scheduler's `interactive-builder` secret in a protected local file, e.g.
`/etc/dml/secrets/interactive-builder`, and set the Compose host variable:

```dotenv
INTERACTIVE_BUILDER_SECRET_HOST_FILE=/etc/dml/secrets/interactive-builder
```

Use `Docker_Image_Builder/compose.interactive.yaml` in addition to the regular
Compose file when interactive builds are enabled.  The registry credential is
builder-local; give it only the pull/push permissions it needs.

## 3. GPU worker host

This is a host-installed Python service, not a service in the root Compose
file.  Create `/etc/dml/worker.env` from `Worker/.env.example` and run it using
`deploy/interactive/worker/dml-worker.service`.  The current `Worker/main.py`
calls `managed_worker.run()`, which requires both `SCHEDULER_URL` and
`WORKER_SERVICE_CREDENTIAL_FILE`.

```dotenv
SCHEDULER_URL=https://scheduler.example.internal
WORKER_SERVICE_CREDENTIAL_FILE=/etc/dml/worker-service.secret
WORKER_SCHEDULER_CA_FILE=/etc/dml/control-ca.pem    # omit for a public CA
WORKER_ID_FILE=/var/lib/dml-worker/worker-id
WORKER_STATE_DIR=/var/lib/dml-worker
WORKER_API_HOST=127.0.0.1
WORKER_API_PORT=8600
WORKER_ASSIGNMENT_LEASE_SECONDS=45
MAX_CONCURRENT_JOBS=2
DOCKER_DATA_ROOT=/var/lib/docker
```

Provision `/var/lib/dml-worker/worker-id` with a UUID.  On the Scheduler, add
that exact UUID to `worker-credentials.json`; on this worker put only the
matching secret in `/etc/dml/worker-service.secret` (no trailing newline).
The worker ID, Scheduler map entry, and worker secret must be provisioned as one
set before first start.

For normal batch execution, the worker also needs routable object-store access:

```dotenv
OBJECT_STORE_URL=https://objects.example.internal
OBJECT_OUTPUT_BUCKET=outputs
OBJECT_STORE_LARGE_FILE_THRESHOLD=52428800         # optional
CONTAINER_AS_ROOT=0                                 # recommended
```

For interactive runtime, additionally set `INTERACTIVE_WORKER_ENABLED=1`, the
registry allowlist/pull credential, pinned access and preflight image digests,
and (when applicable) the Headscale CA, as shown in `Worker/.env.example`.
The registry file is a worker-local `0600` JSON file with
`{"server":"…","username":"…","password":"…"}` and pull-only rights.
Workers must never receive Headscale admin, gateway, bootstrap, controller, or
builder credentials.

## 4. UI machines

The User and Admin Vite applications require no secrets.  Create a local `.env`
in each UI project before building:

```dotenv
# UI/User/.env
VITE_API_URL=https://scheduler.example.internal

# UI/Admin/.env
VITE_API_BASE_URL=https://scheduler.example.internal
```

Vite embeds `VITE_*` values into browser code, so never put passwords, API keys,
or service credentials in them.  The Worker Electron UI is configured through
the process environment, not a Vite variable: set
`WORKER_API_URL=http://127.0.0.1:8600` when it runs on the same worker, or a
secured reachable worker-agent URL when it runs elsewhere.  Its README still
describes the UI as simulated, so it does not need Scheduler credentials.

## Security note

The existing `Docker_Image_Builder/.env` contains a Docker registry token.
Treat it as exposed if this checkout has been shared, committed, logged, or
copied, rotate it at the registry, replace the local file, and ensure `.env` and
all secret directories are ignored by version control.  Do not copy that token
to any other machine unless that machine actually performs image builds.
