# Implementation Plan: Interactive SSH Sessions over Headscale/Tailscale

> Status: Planning complete. The "Interactive Image Builder" is DONE. This plan
> covers the remaining components. Confirmed decisions are recorded in
> `## Confirmed Decisions` below.

---

## Confirmed Decisions

1. **Headscale management** — Build a small `headscale_mgmt/` microservice that
   wraps the Headscale functionality the Scheduler needs (create / revoke
   pre-auth keys). It is a new standalone service.
2. **Gateway is a Headscale node** — The Gateway container runs its own
   `tailscaled` (requires `/dev/net/tun`, `NET_ADMIN`, `NET_RAW`, and its own
   pre-auth key to join the tailnet). This is what lets it reach `100.x.x.x`
   container IPs.
3. **Language / framework** — Python + FastAPI for the new `gateway/` and
   `headscale_mgmt/` services, matching the existing `Scheduler/` and
   `Worker/`.
4. **Scope** — Implement Gateway + Scheduler extensions + Worker extensions +
   Headscale mgmt + interactive-container runtime wiring (the
   `interactive-entrypoint.sh` already exists from the image builder).
   Immediate verifiable goal: **Gateway → (tailnet) → interactive container SSH
   reachability**. The user-facing connection mechanism is OUT of scope for now.

---

## Architecture

```
                    ┌───────────────┐
                    │     User      │
                    └───────┬───────┘
                            │  (out of scope for now)
                            ▼
                    ┌───────────────┐
                    │   Scheduler   │
                    └───┬───────┬───┘
                        │       │
              SSH key   │       │ auth key
                        ▼       ▼
                   ┌──────┐  ┌──────────┐
                   │Gateway│  │ Headscale│  (Headscale mgmt service wraps this)
                   └───┬──┘  └────┬─────┘
                       │           │
                       ▼           ▼
                    ┌─────────────────┐
                    │     Worker      │
                    │  Docker         │
                    │  ┌───────────┐  │
                    │  │Container  │  │
                    │  │ Tailscale │  │
                    │  │ SSHD      │  │
                    │  └───────────┘  │
                    └─────────────────┘
                            ▲
                            │ Headscale network (100.x.x.x)
```

Security boundaries (per user refinement):
- The **Gateway** owns SSH credentials (generates + keeps private keys).
- **Headscale key issuance** is behind the small authenticated
  `headscale_mgmt` service; the Scheduler never handles raw Headscale keys
  longer than necessary.

---

## Components & File-by-File Breakdown

### 1. `headscale_mgmt/` (NEW microservice)

Wraps Headscale management needed by the Scheduler.

| File | Responsibility |
|------|----------------|
| `headscale_mgmt/requirements.txt` | `fastapi`, `uvicorn[standard]`, `pydantic` |
| `headscale_mgmt/Dockerfile` | Python 3.11-slim, installs `headscale` CLI, copies source, runs `uvicorn` |
| `headscale_mgmt/config.py` | Env: `HEADSCALE_API_URL` or `HEADSCALE_CLI_PATH` (default `headscale`), `HEADSCALE_USER` (default `sandbox`), `AUTH_TOKEN` (shared secret the Scheduler uses), `PREAUTH_KEY_EXPIRY` (default 3600), `LISTEN_HOST`/`LISTEN_PORT` (default `0.0.0.0:8100`) |
| `headscale_mgmt/headscale_client.py` | `HeadscaleClient`: `create_preauth_key(expiry_seconds, user, reusable, ephemeral) -> str` (runs `headscale preauthkeys create ...` via `subprocess`), `revoke_key(key) -> None` (runs `headscale preauthkeys expire --key <key>`) |
| `headscale_mgmt/api.py` | `POST /auth-keys` (validates `Authorization: Bearer <AUTH_TOKEN>`, body `{"expiry_seconds":3600,"reusable":true,"ephemeral":true}`, returns `{"auth_key":"..."}`); `DELETE /auth-keys/{key}` (revoke) |
| `headscale_mgmt/main.py` | FastAPI app, CORS, router, no-op lifespan |

### 2. `gateway/` (NEW microservice — separate directory)

Owns SSH credentials; is itself a Headscale node; reaches containers over the tailnet.

| File | Responsibility |
|------|----------------|
| `gateway/requirements.txt` | `fastapi`, `uvicorn[standard]`, `paramiko`, `pydantic` |
| `gateway/Dockerfile` | Python 3.11-slim, installs `openssh-client` + `tailscale` (official install script), copies source, runs `uvicorn`. MUST be granted `/dev/net/tun`, `NET_ADMIN`, `NET_RAW` (document in comments / compose) |
| `gateway/config.py` | Env: `GATEWAY_API_PORT` (8200), `SSH_KEY_DIR` (default `/data/ssh-keys`), `TAILSCALE_AUTH_KEY` (Gateway's own pre-auth key), `HEADSCALE_URL`, `GATEWAY_HOSTNAME` (default `gateway`), `SCHEDULER_API_URL` (optional), `SSH_CONNECT_TIMEOUT` (10) |
| `gateway/ssh_key_manager.py` | `SSHKeyManager`: `generate_keypair(session_id) -> (priv_pem, pub_openssh)` (ED25519, private key saved at `<SSH_KEY_DIR>/<session_id>` mode `0o600`); `get_private_key_path`, `get_public_key`, `delete_keypair` |
| `gateway/session_client.py` | `SessionClient`: `connect(session_id, target_ip) -> paramiko.SSHClient` (connects `sandbox@{target_ip}:22` using the session's private key, `AutoAddPolicy` for ephemeral host key, `connect_timeout` from config); `execute_command(client, command) -> str`; `close(client)` |
| `gateway/api.py` | `POST /keys` (`{"session_id":...}` → `{"session_id","public_key"}`); `DELETE /keys/{session_id}`; `POST /connect` (`{"session_id","target_ip","command"}` → runs command over SSH, returns stdout — used to verify reachability); `GET /health` |
| `gateway/main.py` | Startup: ensure key dir exists, launch `tailscaled`, run `tailscale up --login-server=<HEADSCALE_URL> --authkey=<TAILSCALE_AUTH_KEY> --hostname=<GATEWAY_HOSTNAME>`; Shutdown: stop tailscale; CORS; include router |

### 3. `Scheduler/` (EXTEND)

| File | Responsibility |
|------|----------------|
| `Scheduler/app/models/interactive_session_model.py` | `InteractiveSession` ORM: `id` (UUID PK), `job_id` (FK jobs.id), `user_id`, `base_job_id`, `session_id` (UUID unique — tailnet hostname), `gateway_session_id`, `worker_id`, `headscale_ip` (nullable), `ssh_public_key`, `headscale_auth_key` (temp), `status` enum (`PENDING`/`DEPLOYING`/`RUNNING`/`STOPPED`/`FAILED`), `created_at`/`updated_at`/`stopped_at` |
| `Scheduler/app/schemas/interactive_schema.py` | `CreateInteractiveSessionRequest` (`{base_job_id}`), `CreateInteractiveSessionResponse` (`{session_id,job_id,status}`), `InteractiveSessionReport` (`{session_id,headscale_ip,status}` — Worker→Scheduler), `InteractiveSessionStatus` |
| `Scheduler/app/db/database.py` | Migration: create `interactive_sessions` table (+ any needed columns) |
| `Scheduler/app/services/interactive_service.py` | `InteractiveService`: `create_session(db,user_id,base_job_id)` → (a) create interactive job record, (b) `POST /keys` to Gateway → `session_id`+`public_key`, (c) `POST /auth-keys` to headscale_mgmt → pre-auth key, (d) select worker (prefer the worker that ran the base job, else first online), (e) `POST /api/interactive/run` to worker with `{flag:"interactive",session_id,image_tag,headscale_url,headscale_auth_key,ssh_public_key}`, (f) create `InteractiveSession` status `DEPLOYING`; `update_session_ip(db,session_id,headscale_ip)` → `RUNNING`; `stop_session(db,session_id)` → `STOPPED` + notify Gateway delete key + headscale_mgmt revoke key; `get_session`; `_dispatch_to_worker(worker_host,worker_port,payload)` |
| `Scheduler/app/api/interactive_route.py` | Router (prefix `/interactive`): `POST /interactive/create`, `POST /interactive/report_ip` (Worker reports IP), `GET /interactive/sessions`, `GET /interactive/{session_id}`, `POST /interactive/{session_id}/stop` |
| `Scheduler/app/main.py` | `include_router(interactive_router, prefix="/interactive", tags=["interactive"])` |
| `Scheduler/app/services/job_service.py` | Add `JobStatus` values `INTERACTIVE_DEPLOYING`/`INTERACTIVE_RUNNING`/`INTERACTIVE_STOPPED`; flag routing for `interactive` (bookkeeping; dispatch is push-based) |
| `Scheduler/.env` (or config) | `GATEWAY_API_URL=http://gateway:8200`, `HEADSCALE_MGMT_URL=http://headscale-mgmt:8100`, `HEADSCALE_MGMT_AUTH_TOKEN=...`, `HEADSCALE_URL=https://headscale.example.com` |

### 4. `Worker/` (EXTEND)

| File | Responsibility |
|------|----------------|
| `Worker/interactive_handler.py` | `run_interactive_container(api, session_id, image_tag, headscale_url, headscale_auth_key, ssh_public_key)`: (1) pull interactive image, (2) `docker run -d --rm --device /dev/net/tun --cap-add NET_ADMIN --cap-add NET_RAW -e HEADSCALE_URL=... -e HEADSCALE_AUTHKEY=... -e SESSION_ID=... -e SSH_PUBLIC_KEY="..." <image_tag>` — **use the EXACT env var names the existing `interactive-entrypoint.sh` expects**, (3) poll `docker logs` for the `"Tailscale IP: 100."` line the entrypoint prints; capture `100.x.x.x`; timeout ~30s → fail, (4) `POST /interactive/report_ip` to Scheduler, (5) monitor container; report `stopped` if it exits, (6) return container id |
| `Worker/executor.py` | Add `handle_interactive(job)` delegating to `interactive_handler`; flag routing `elif flag == "interactive": self.handle_interactive(job)` |
| `Worker/api.py` (or `Worker/server.py`) | Add `report_interactive_ip(session_id, headscale_ip)` → Scheduler `/interactive/report_ip`; add route `POST /api/interactive/run` (accepts payload, runs `run_interactive_container` in a background thread, returns `{session_id,status:"deploying"}`) |
| `Worker/config.py` | Add interactive Docker run config constants if needed |

### 5. Interactive Container runtime (already built by Image Builder)

The `interactive-entrypoint.sh` (already done) must, at container start:
- start `tailscaled`
- run `tailscale up --login-server=<HEADSCALE_URL> --authkey=<HEADSCALE_AUTHKEY>`
- install the Gateway-provided public key into `/home/sandbox/.ssh/authorized_keys`
- start `sshd`
- print `Tailscale IP: 100.x.x.x` (so the Worker can detect it)
- NOT auto-start the user's training job

The Worker must pass exactly the env vars this entrypoint reads.

### 6. Docker Compose

- Add `gateway` and `headscale-mgmt` services.
- `gateway` MUST have `/dev/net/tun`, `NET_ADMIN`, `NET_RAW`, a volume
  `gateway-keys:/data/ssh-keys`, and env `TAILSCALE_AUTH_KEY`, `HEADSCALE_URL`.
- `headscale-mgmt` needs the `headscale` CLI and `AUTH_TOKEN`.

---

## Implementation Order

1. `headscale_mgmt/` (standalone, no deps on others)
2. `gateway/` (standalone; joins tailnet on startup)
3. Scheduler data model + schemas + migration
4. Scheduler `interactive_service.py` + routes + `main.py` wiring + config
5. Worker `interactive_handler.py` + `executor.py` + API route + config
6. Docker Compose wiring
7. End-to-end verification (see criteria)

---

## Done Criteria (verification targets)

1. All listed files exist with the described functionality.
2. `headscale_mgmt` starts and `POST /auth-keys` returns a valid pre-auth key.
3. `gateway` starts, joins the tailnet, and `POST /keys` returns a generated SSH keypair.
4. Scheduler `POST /interactive/create` triggers the full chain: Gateway key gen → Headscale auth key → worker dispatch.
5. Worker runs an interactive container with `/dev/net/tun`, `NET_ADMIN`, `NET_RAW`, and the required env vars.
6. The container's `interactive-entrypoint.sh` starts tailscaled, connects to Headscale, installs the SSH public key, starts sshd.
7. Worker detects the container's Headscale IP (`100.x.x.x`) and reports it to Scheduler via `POST /interactive/report_ip`.
8. **Gateway can SSH into the container** via `POST /connect` with
   `{"session_id":"...","target_ip":"100.x.x.x","command":"echo hello"}` and
   receives `"hello"` back. ← primary goal.
9. Existing training job workflows continue to work unchanged (regression check).

---

## Risks / Open Questions

- **Gateway tailnet join**: needs its own pre-auth key (provision out-of-band / long-lived reusable key). If Headscale mgmt is down, Gateway can't reach containers.
- **Headscale CLI vs API**: plan uses CLI via `subprocess`; if Headscale runs in a separate container, the CLI must be pointed at it (`--server ... --api-key ...`).
- **SSH known_hosts**: use `AutoAddPolicy` for ephemeral container host keys (acceptable for MVP).
- **IP detection timing**: poll `docker logs` for the `"Tailscale IP: 100."` line; fail after ~30s.
- **Security**: Headscale pre-auth key is passed Scheduler → Worker; acceptable for MVP, should be short-lived in production.
- **Live connectivity** (criteria 2/3/8) requires a running Headscale + tailnet and cannot be fully verified in a sandbox without those; note what was and wasn't verifiable.
