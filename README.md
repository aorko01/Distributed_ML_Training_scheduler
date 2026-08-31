# Distributed ML Training Scheduler

A small, self-hosted platform for running GPU training jobs and short-lived
interactive shells across a cluster of worker nodes. Users submit a zip of
their code, the system builds a Docker image, schedules the job on a worker
that has a free GPU, and (optionally) hands out an SSH shell into the
running container.

The repo is laid out as a set of cooperating FastAPI microservices that share
a single PostgreSQL database and a single Redis instance.

## TL;DR

```
                 ┌────────────┐     ┌───────────────┐
                 │  Frontend  │────▶│  Scheduler    │ (FastAPI + Postgres + Redis)
                 │  (UI/)     │     │  /jobs, /auth │
                 └────────────┘     │  /interactive │
                          ▲         │  /workers …   │
                          │         └──┬────────┬───┘
                          │            │        │
              pull_job    │            │        │ ask for SSH key
        ┌─────────────────┘            │        ▼
        │                              │   ┌─────────┐         ┌─────────────┐
        ▼                              │   │ Gateway │─SSH─▶   │ Headscale   │
 ┌────────────┐      builds images     │   │(bastion)│         │ mgmt        │
 │  Worker(s) │◀──────────────────┐    │   └────┬────┘         └────┬────────┘
 │  docker,   │                   │    │        │ 100.x.x.x         │ pre-auth
 │  GPU,      │    ┌──────────────┴─┐  │        ▼                   │ key
 │  nsenter   │───▶│ Docker Image  │   │   ┌─────────────┐            ▼
 └────────────┘    │ Builder       │   │   │  Worker     │     ┌─────────────┐
        │          └───────────────┘   │   │  interactive│◀─── │ Headscale   │
        │                              │   │  container  │     │ control     │
        ▼                              │   │  + access   │     │ plane       │
 ┌────────────┐    blobs               │   │  container  │     └─────────────┘
 │ Object    │◀─────────────────────── ┘   └─────────────┘
 │ Store     │
 │ (MinIO)   │
 └────────────┘
```

## Services

Everything below is a backend service. The `UI/` directory is the React/TS
frontend and is intentionally out of scope for this README.

| Directory              | What it is                                                                |
|---|---|
| `Scheduler/`           | FastAPI control plane: auth, jobs, workers, scheduling, interactive sessions, watchdog |
| `Worker/`              | Agent that runs on each GPU node: registers, pulls jobs, runs Docker containers, streams logs |
| `Docker_Image_Builder/`| Polls Scheduler for unbuilt jobs, builds + pushes Docker images to Docker Hub |
| `Object_store/`        | FastAPI in front of MinIO; upload / download / presigned URLs / listing |
| `gateway/`             | SSH bastion. Owns per-session SSH keys, runs its own Tailscale node, proxies SSH into interactive containers |
| `headscale_mgmt/`      | Thin wrapper over Headscale's CLI/REST for minting + revoking Tailscale pre-auth keys |
| `AccessContainer/`     | Dockerfile + entrypoint for the shared `aorko123/access-sshd:latest` image used by interactive sessions |

`Scheduler/docker-compose.yml` brings up `db` (Postgres), `redis`,
`api` (Scheduler), `headscale-mgmt`, `gateway`, and `pgadmin`.
`Object_store/docker-compose.yml` brings up `minio` + `object-store`.
Workers run on separate hosts with their own `docker run` / systemd unit;
they are not part of `Scheduler/docker-compose.yml`.

## How a job flows

### 1. Submit

- User authenticates via `POST /auth/login` → JWT.
- `POST /jobs/submit_job` with a zip (`requirements.txt` required),
  command, and `docker_base_image`. The Scheduler:
  - uploads the zip to the Object Store (`POST /objects/upload`),
  - creates a `Job` row (`status = NOT_RUNNABLE`).
- The Dashboard polls `/jobs/mine` and shows a live log feed over
  `WS /jobs/{job_id}/logs/stream` (Redis Streams under the hood).

### 2. Build image

- `Docker_Image_Builder/builder.py` polls `GET /jobs/unbuilt_jobs`,
  downloads the archive, generates a Dockerfile, builds the image, and
  pushes it to Docker Hub as `<DOCKER_HUB_USERNAME>/<job_id>:latest`.
- Streams build output to `POST /jobs/logs/{job_id}` and uploads a
  throttled `build.log` to the Object Store.
- On success calls `POST /jobs/update_job_to_vram_estimation_pending` →
  status `VRAM_ESTIMATION_PENDING`.

### 3. VRAM estimation

- Workers calling `POST /jobs/pull_job` get dispatched either a training
  job, a retry, a VRAM-estimation probe, or an interactive session. The
  scheduler picks the worker with the highest free VRAM for the probe.
- The worker runs `Worker/vram_estimation.py` inside the user's image
  (it monkey-patches `torch.optim.Optimizer.__init__`, runs the user's
  script with `runpy`, then exits after a few steps). Result is posted
  back as `POST /jobs/save_vram_estimation{peak VRAM, RAM, step_time}`
  → status `RUNNABLE`.

### 4. Schedule + execute

- Workers poll `POST /jobs/pull_job{worker_id, gpu_type, free_vram}`
  every `JOB_POLL_INTERVAL` (default 10s).
- The Scheduler picks in this order: (1) `INTERACTIVE_READY` session on
  an idle worker (failover-aware: won't redispatch to the worker that
  just lost it), (2) VRAM estimation on the highest-free-VRAM worker,
  (3) `RETRY_NEEDED` jobs, (4) `RUNNABLE` training jobs (ordered by
  `vram_required DESC`, then `created_at ASC`).
- The Worker pulls the image from Docker Hub, mounts a per-job output
  dir, runs the user's command, streams logs back, and continuously
  uploads changed files to the Object Store via
  `OutputFileMonitor` (every 2 s).
- On restart, a worker first calls `POST /jobs/resume` to reclaim its
  in-progress job before the scheduler's stall watchdog requeues it.

### 5. Complete / fail

- `POST /jobs/mark_completed` → `COMPLETED`, computes `gpu_hour`.
- `POST /jobs/mark_failed{failure_type: "user"|"system"}` → `FAILED` or
  `RETRY_NEEDED` (system failures are re-dispatched later).
- A background watchdog (`Scheduler/app/services/watchdog_service.py`,
  started in the FastAPI lifespan) requeues any job whose worker stopped
  heartbeating for >180 s.

## Interactive sessions (SSH over Headscale)

Interactive sessions let a user SSH into a long-running container on a
worker, with no public inbound port on the worker and no SSH keys on the
user side. The components:

```
User ──ssh──▶ Gateway (Tailscale node, port 2222)
                 │   on login, validate one-time password against
                 │   Scheduler's /interactive/sessions/verify-ephemeral
                 │   and learn session_id + headscale_ip (100.x.x.x)
                 ▼
              Container (worker, nsenter'd via the shared access image)
                 │   headscale tailscaled on the same tailnet
                 │   sshd ForceCommand → nsenter -t 1 ... → env container
```

Key data model: `Scheduler/app/models/interactive_session_model.py`
(`InteractiveSession` with `session_id`, `job_id`, `user_id`,
`headscale_ip`, `worker_id`, `last_worker_id`, `ssh_public_key`,
`headscale_auth_key`, and a 6-state enum).

1. **Create.** `POST /jobs/submit_interactive` (derived from an existing
   training job's image) or `POST /jobs/submit_interactive_direct`
   (with a fresh upload + env spec).
   `Scheduler/app/services/interactive_service.py:create_session`:
   - creates a `Job` row,
   - asks `gateway` for a session SSH keypair (`POST /keys`),
   - asks `headscale_mgmt` for a Tailscale pre-auth key
     (`POST /auth-keys`, default 1 h TTL),
   - persists `InteractiveSession` as `PENDING`.
2. **Build.** `Docker_Image_Builder` builds the env image (or reuses
   the base job's image) and posts
   `POST /jobs/mark_interactive_ready` → `INTERACTIVE_READY`.
3. **Dispatch.** First idle worker calling `/jobs/pull_job` claims the
   session (`SELECT … FOR UPDATE`), sets `INTERACTIVE_DEPLOYING`, and
   gets a payload with `env_image_tag`, `access_image_tag=aorko123/access-sshd:latest`,
   `headscale_url`, `headscale_auth_key`, `ssh_public_key`.
4. **Deploy.** `Worker/interactive_handler.py` launches a clean env
   container (`sleep infinity`) and an access container with
   `--pid container:<env> --cap-add ALL --device /dev/net/tun`. The
   access entrypoint (`AccessContainer/access-entrypoint.sh`) starts
   `tailscaled`, joins Headscale, installs the gateway's public key
   for `sandbox`, and starts sshd with `ForceCommand nsenter -t 1 -m
   -u -i -n -p -- /bin/bash -l` so logins land inside the env container.
   The worker polls `docker logs` for `Tailscale IP: 100.x.x.x` and
   posts it to `POST /interactive/report_ip` → `INTERACTIVE_RUNNING`.
5. **Connect.** `GET /jobs/{job_id}/connect` (JWT) returns the
   gateway host/port, the user's username, `container_user=sandbox`,
   and a one-time 5-minute password minted by
   `Scheduler/app/services/ephemeral_password_service.py` (in-memory,
   SHA-256 keyed, single-use, one-valid-per-session).
   The gateway's `check_auth_password` calls
   `POST /interactive/sessions/verify-ephemeral` on the Scheduler,
   consumes the password, and is told the `session_id` and
   `headscale_ip`. `gateway/ssh_server.py` then opens an upstream SSH
   channel to the container and proxies bytes bidirectionally (including
   window-resize pty requests).
6. **Stop.** `POST /interactive/{session_id}/stop` flags the session
   `STOPPING`. Because workers are not directly reachable, the stop
   command rides on the worker's next heartbeat response in
   `stop_sessions[]`; the worker tears down the containers and reports
   `STOPPED`. Keys on the gateway are deleted and the Headscale key is
   revoked.

Auto-kill timers in the watchdog:
- 10 min after start with no SSH connect,
- 6 h hard cap from start,
- 30 min idle (no login) after connecting.

Failover: if a worker or its container stops heartbeating, the session
is reset to `PENDING` and `last_worker_id` is remembered so the next
dispatch avoids the same machine.

## Persistence

| Service              | Storage                                            |
|---|---|
| `Scheduler`           | PostgreSQL (`jobs`, `users`, `workers`, `interactive_sessions`, `resource_requests`) + Redis (heartbeats, log streams, `job_worker:<id>` mapping, `interactive_heartbeat:<sid>`) |
| `Worker`              | Local Docker + per-job output dir + `running_job.json` (crash-recovery marker) |
| `Object_store`        | MinIO bucket `uploads/` (source archives) and `outputs/` (training artifacts, build logs) |
| `Docker_Image_Builder`| Local SQLite (`processed_jobs`, `base_images`) for de-dup + pruning |
| `gateway`             | Local volume `gateway-keys` (`/data/ssh-keys/`) — session Ed25519 privkeys + the gateway host key |

## Auth model

- **Users → Scheduler.** JWT (HS256, 30-min default). Decoded in
  `Scheduler/app/utils/auth.py` and `app/api/deps.py`. WebSocket
  endpoints accept the token via the `?token=` query param.
- **Workers → Scheduler.** No JWT; identity is the worker UUID and the
  network boundary. Heartbeats ride `/workers/heartbeat`. Stop commands
  for the worker ride in the heartbeat response (`stop_sessions[]`).
- **Internal callbacks** (`/jobs/update_job_to_vram_estimation_pending`,
  `/jobs/mark_interactive_ready`, `/jobs/mark_failed`, `/jobs/save_vram_estimation`,
  `/interactive/report_ip`) are unauthenticated and rely on the same
  network boundary.
- **Gateway → Scheduler.** The gateway posts
  `/interactive/sessions/verify-ephemeral{username, password}` and
  learns the `session_id`/`headscale_ip` on success.
- **`headscale_mgmt` → Scheduler.** `Authorization: Bearer
  ${HEADSCALE_MGMT_AUTH_TOKEN}`.

## Configuration

All services are configured via environment variables; the canonical list
lives in each service's `config.py`. Key variables (defaults shown):

| Var                                | Default                          | Used by        |
|---|---|---|
| `DATABASE_URL`                     | `postgresql://admin:admin@db:5432/app` | Scheduler  |
| `REDIS_HOST` / `REDIS_PORT`        | `redis` / `6379`                 | Scheduler      |
| `JWT_SECRET_KEY` / `JWT_ALGORITHM` | `your-secret-key-change-in-production` / `HS256` | Scheduler |
| `OBJECT_STORE_URL`                 | `http://localhost:8010`          | Scheduler, Worker, Builder |
| `OBJECT_STORE_BUCKET` / `OBJECT_OUTPUT_BUCKET` | `uploads` / `outputs` | Scheduler, Builder |
| `GATEWAY_API_URL`                  | `http://gateway:8200`            | Scheduler      |
| `HEADSCALE_MGMT_URL`               | `http://headscale-mgmt:8100`     | Scheduler      |
| `HEADSCALE_MGMT_AUTH_TOKEN`        | `change-me`                      | Scheduler, headscale_mgmt |
| `HEADSCALE_URL`                    | `https://headscale.example.com`  | Scheduler, Worker, gateway |
| `GATEWAY_PUBLIC_HOST`              | `""`                              | Scheduler, frontend |
| `GATEWAY_SSH_PORT` / `GATEWAY_PUBLIC_SSH_PORT` | `2222` / `443`         | Scheduler, gateway |
| `SCHEDULER_URL`                    | (required)                       | Worker         |
| `DOCKER_HUB_USERNAME` / `DOCKER_HUB_PASSWORD` | `aorko123` / `""`        | Worker, Builder |
| `AUTH_TOKEN`                       | `change-me`                      | headscale_mgmt |

## Where to look (file-level index)

### Scheduler (`Scheduler/app/`)
- `main.py` — FastAPI app, lifespan starts the watchdog, mounts routers.
- `db/database.py` — SQLAlchemy engine + lightweight additive migrations.
- `core/redis.py` — shared async Redis client.
- `api/jobs_route.py` — job submit / pull / resume / logs / connect info.
- `api/interactive_route.py` — interactive create / report_ip / verify-ephemeral / stop.
- `api/auth_route.py`, `api/worker_route.py`, `api/scheduler_route.py`,
  `api/docker_route.py`, `api/resource_route.py`, `api/deps.py` — rest of the HTTP surface.
- `services/job_service.py` — scheduling strategies and state transitions.
- `services/interactive_service.py` — full interactive-session orchestration.
- `services/ephemeral_password_service.py` — single-use SSH password store.
- `services/watchdog_service.py` — stalled jobs, stalled sessions, auto-kill timers.
- `services/worker_service.py`, `services/log_service.py`,
  `services/scheduler_service.py`, `services/resource_service.py`,
  `services/auth_service.py` — supporting services.
- `models/` — `user_model.py`, `worker_model.py`, `job_model.py`,
  `interactive_session_model.py`, `resource_request_model.py`.
- `schemas/` — Pydantic request/response models for every route.
- `utils/auth.py` — bcrypt + JWT helpers; `utils/file_utils.py` — zip
  validation + Object Store upload.

### Worker (`Worker/`)
- `main.py` — entrypoint, three daemon threads (heartbeat, job poll, API server).
- `server.py` — local FastAPI dashboard (`/api/worker`, `/api/metrics`,
  `/api/gpus`, `/ws/metrics`, pause/resume, `/api/config`).
- `api.py` — `SchedulerAPI` HTTP client.
- `executor.py` — flag dispatcher; `handle_training`, `handle_retry`,
  `handle_vram_estimation`, `handle_interactive`, plus
  `resume_persisted_job_if_any`.
- `interactive_handler.py` — two-container SSH-over-Tailnet launcher.
- `output_monitor.py` — background uploader of job artifacts.
- `object_store.py` — MinIO client with presigned URL bypass for >50 MB.
- `job_state.py` — atomic `running_job.json` for crash recovery.
- `io_monitor.py`, `telemetry.py`, `runtime_config.py`,
  `hardware.py`, `vram_estimation.py`, `config.py`.

### Docker_Image_Builder
- `builder.py` — poll loop; per-tick `scan_and_process`.
- `docker_ops.py` — Dockerfile generation, Docker SDK calls, log streaming.
- `api.py` — HTTP client wrappers for the Scheduler + Object Store.
- `database.py` — SQLite tracker.
- `config.py`.

### gateway (`gateway/`)
- `main.py` — FastAPI + lifespan (start Tailscale, start SSH server).
- `api.py` — `/health`, `/keys`, `/keys/{id}`, `/connect`.
- `ssh_server.py` — paramiko SSH bastion (`GatewayServer`,
  `_proxy_shell`, `start_ssh_server`).
- `session_client.py` — outbound SSH to containers.
- `ssh_key_manager.py` — per-session Ed25519 keypairs.
- `config.py`.

### headscale_mgmt
- `main.py` — FastAPI app + CORS.
- `api.py` — `/auth-keys`, `/auth-keys/{key}` with bearer-token auth.
- `headscale_client.py` — CLI-or-HTTP abstraction over Headscale.
- `schemas.py`, `config.py`.

### Object_store
- `main.py` — FastAPI wrapper over MinIO with both internal and public endpoints.
- `init_buckets.py` — idempotent bucket creation.
- `docker-compose.yml` — `minio` + `object-store`.

### AccessContainer
- `Dockerfile` + `access-entrypoint.sh` — the shared
  `aorko123/access-sshd:latest` image (sshd + tailscaled + nsenter).

## Quick start

```bash
# 1. Object store (MinIO + FastAPI wrapper)
cd Object_store && docker compose up -d

# 2. Scheduler + gateway + headscale-mgmt + postgres + redis
cd ../Scheduler
# .env must set at minimum: JWT_SECRET_KEY, PGADMIN_DEFAULT_PASSWORD,
# HEADSCALE_URL, HEADSCALE_API_KEY, GATEWAY_TAILSCALE_AUTH_KEY,
# GATEWAY_PUBLIC_HOST, POSTGRES_PASSWORD.
docker compose up -d --build

# 3. Docker Image Builder (separate host or sidecar)
cd ../Docker_Image_Builder && docker compose up -d --build

# 4. Worker (on each GPU host)
cd ../Worker
# SCHEDULER_URL=http://<scheduler-host>:8000 in .env, then:
python main.py
```

## Notes / non-goals

- CORS is wide open in every service (`allow_origins=["*"]`); tighten for real deployments.
- Schema migrations are lightweight `ADD COLUMN IF NOT EXISTS`; use Alembic for production.
- Internal worker/builder callbacks are unauthenticated; rely on the network boundary.
- The shared `aorko123/access-sshd:latest` image is the only path into an
  interactive container; it `ForceCommand`s the shell through `nsenter`
  into the env container's PID 1.
- Workers have no inbound-reachable endpoint — all commands ride on
  heartbeat responses.