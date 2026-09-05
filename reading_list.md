# Interactive Session: Image Creation, Container Running & Command Forwarding

Reading plan organized by the lifecycle phases of an interactive session.

---

## Phase 1: Entry Point — User Submits a Session

| # | File | What to look for |
|---|------|------------------|
| 1 | `UI/User/src/pages/Machines.tsx` | Two creation modes: "From Existing Job" vs "Direct Upload". How base images, GPU/VRAM/RAM specs are selected. Calls `submitInteractiveSession` / `submitInteractiveDirect`. |
| 2 | `UI/User/src/services/jobs.ts` | `submitInteractiveSession`, `submitInteractiveDirect`, `fetchJobConnectInfo`, `commitInteractiveJob` — the frontend API surface. |
| 3 | `Scheduler/app/api/jobs_route.py` | `POST /submit_interactive`, `POST /submit_interactive_direct`, `POST /mark_interactive_ready`, `POST /{job_id}/commit`, `GET /{job_id}/connect`. |
| 4 | `Scheduler/app/api/interactive_route.py` | `POST /create`, `POST /report_ip`, `GET /sessions`, `POST /{session_id}/stop`. |
| 5 | `Scheduler/app/schemas/interactive_schema.py` | Pydantic request/response shapes: `CreateInteractiveSessionRequest`, `CreateInteractiveSessionResponse`. |
| 6 | `Scheduler/app/services/interactive_service.py` | Core orchestration: creates InteractiveSession (PENDING), asks Gateway for SSH keys, asks Headscale for pre-auth key, updates IP/status, stops sessions, delivers stop/commit commands via heartbeat. |
| 7 | `Scheduler/app/services/job_service.py` | `_check_interactive_job_strategy` — dispatches INTERACTIVE_READY sessions to idle workers (failover-aware). Also `commit_interactive_job`, `complete_commit`, `fail_commit`. |
| 8 | `Scheduler/app/models/interactive_session_model.py` | SQLAlchemy model: 6-state lifecycle (PENDING → DEPLOYING → RUNNING → STOPPING → STOPPED → FAILED). Fields: session_id, job_id, headscale_ip, ssh_public_key, worker_id, etc. |
| 9 | `Scheduler/app/models/job_model.py` | Job model with interactive status enums (`INTERACTIVE_READY`, `INTERACTIVE_DEPLOYING`, `INTERACTIVE_RUNNING`, `INTERACTIVE_STOPPED`), `build_type` field. |
| 10 | `Scheduler/app/services/ephemeral_password_service.py` | One-time password issuance for SSH gateway auth (TTL'd, SHA-256 keyed). |

---

## Phase 2: Image Creation — Docker_Image_Builder

| # | File | What to look for |
|---|------|------------------|
| 11 | `Docker_Image_Builder/builder.py` | Main poll loop: fetches unbuilt jobs, downloads archive, generates Dockerfile, builds image, pushes to Docker Hub, notifies scheduler. Handles both training and interactive build types. |
| 12 | `Docker_Image_Builder/docker_ops.py` | `generate_dockerfile` (training), `generate_env_dockerfile` (interactive — no SSH/Headscale, just `sleep infinity`), `resolve_interactive_base_image` (base image resolution), `build_image`, `push_image`, `ensure_access_image` (pulls access-sshd:latest). |
| 13 | `Docker_Image_Builder/api.py` | HTTP client: `fetch_unbuilt_jobs`, `download_job_archive`, `notify_scheduler_job_ready`, `notify_scheduler_interactive_ready`. |
| 14 | `Docker_Image_Builder/config.py` | Builder configuration (Docker Hub creds, scheduler URL, object store URL). |

---

## Phase 3: Container Running — Worker Deploys the Session

| # | File | What to look for |
|---|------|------------------|
| 15 | `Worker/executor.py` | `process_job` dispatches "interactive" flag → `handle_interactive` → calls `interactive_handler.run_interactive_session`. |
| 16 | `Worker/interactive_handler.py` | **Core container launcher.** Two-container model: (1) env container (`sleep infinity`), (2) access container (`--pid container:<env>`, tailscaled, sshd). Polls for Tailscale IP, reports to scheduler, monitors timeouts (session/no-connect/idle), handles `commit_and_push_container`. |
| 17 | `Worker/main.py` | Worker entrypoint. Three daemon threads: heartbeat (sends heartbeat, handles `stop_sessions[]` and `commit_sessions[]` from scheduler response), job poll (pulls jobs, calls executor), container health. |
| 18 | `Worker/api.py` | `SchedulerAPI` HTTP client: `report_interactive_ip`, `commit_complete`, `commit_failed`, `send_heartbeat`. |
| 19 | `Worker/config.py` | Worker env vars: `INTERACTIVE_*`, `DOCKER_HUB_*`, `SCHEDULER_URL`. |
| 20 | `Worker/container_health.py` | Periodic health scanner. Kills orphaned training containers. Skips interactive containers (`interactive-` prefix). |

---

## Phase 4: Access Container — SSH Entry Point

| # | File | What to look for |
|---|------|------------------|
| 21 | `AccessContainer/Dockerfile` | Shared `access-sshd` image: openssh-server, tailscale, nsenter, sandbox user. |
| 22 | `AccessContainer/access-entrypoint.sh` | Starts tailscaled, connects to Headscale, installs gateway SSH public key, configures sshd with `ForceCommand /usr/local/bin/enter-env.sh`. |
| 23 | `AccessContainer/enter-env.sh` | Environment reconstruction: reads `/proc/1/environ` (env container's vars via shared PID namespace), sanitizes, backfills HOME/PATH/TERM, execs `nsenter` into env container's namespaces → drops user into bash. |

---

## Phase 5: Command Forwarding — Gateway Proxies SSH

| # | File | What to look for |
|---|------|------------------|
| 24 | `gateway/ssh_server.py` | Paramiko SSH bastion. Authenticates via ephemeral passwords (validates against scheduler). Proxies channels: `_proxy_shell` (interactive/non-interactive), `_proxy_exec` (command execution), `_proxy_subsystem` (SFTP). Bidirectional byte-level proxy with PTY/window-change support. |
| 25 | `gateway/session_client.py` | Outbound SSH client to containers. `connect()` opens SSH to container IP using per-session Ed25519 key. `execute_command()` runs commands on containers. |
| 26 | `gateway/ssh_key_manager.py` | Per-session Ed25519 keypair generation, storage, and cleanup. |
| 27 | `gateway/api.py` | FastAPI routes: `POST /keys` (generate session keypair), `POST /connect` (execute command on container). |
| 28 | `gateway/main.py` | Gateway entrypoint: starts Tailscale daemon, joins tailnet, starts SSH server. |
| 29 | `gateway/config.py` | Gateway config (SSH key dir, Tailscale auth, scheduler URL, host key path). |

---

## Phase 6: Commit & Stop — Session Lifecycle End

| # | File | What to look for |
|---|------|------------------|
| 30 | `UI/User/src/components/CommitModal.tsx` | Commit UI: run command, resume checkpoint command, priority. |
| 31 | `UI/User/src/components/ConnectOptionsModal.tsx` | SSH connection info display (gateway host/port, one-time password, SSH config for VS Code). |
| 32 | `Scheduler/app/services/worker_service.py` | Worker liveness (Redis TTL), interactive container heartbeat tracking. |
| 33 | `Scheduler/app/services/watchdog_service.py` | Stall detection: STOPPING sessions → finalized, DEPLOYING/RUNNING → requeued to PENDING for failover. |
| 34 | `Worker/job_state.py` | Atomic `running_job.json` persistence for crash recovery. |
| 35 | `Worker/image_cleanup.py` | Docker image removal helpers. Protects base/shared images (access-sshd, pytorch). |

---

## Phase 7: Supporting Infrastructure

| # | File | What to look for |
|---|------|------------------|
| 36 | `headscale_mgmt/api.py` | `/auth-keys` — creates/revokes Tailscale pre-auth keys for access containers. |
| 37 | `headscale_mgmt/headscale_client.py` | CLI-or-HTTP abstraction over Headscale. |
| 38 | `Object_store/main.py` | MinIO wrapper: upload, download, presigned URLs. |
| 39 | `Scheduler/docker-compose.yml` | Main infra: postgres, redis, scheduler API, headscale-mgmt, gateway. |
| 40 | `README.md` | Project overview and architecture. |

---

## Quick Reference: Data Flow Summary

```
User (UI)
  │
  ├─ submitInteractiveSession ──► Scheduler (creates Job + InteractiveSession PENDING)
  │                                 │
  │                                 ├─► Gateway: POST /keys (generate SSH keypair)
  │                                 └─► Headscale: POST /auth-keys (pre-auth key)
  │
  ├─ Docker_Image_Builder polls ──► Builds env image (sleep infinity)
  │                                 └─► Marks INTERACTIVE_READY
  │
  ├─ Worker pulls job ◄────────── Scheduler dispatches via heartbeat pull
  │   │
  │   ├─ interactive_handler.py
  │   │   ├─ docker run env-container (sleep infinity)
  │   │   ├─ docker run access-container (--pid env, tailscaled, sshd)
  │   │   ├─ Poll Tailscale IP
  │   │   └─ POST /report_ip ──► Scheduler (DEPLOYING → RUNNING)
  │   │
  │   └─ Heartbeat loop ◄───────► Scheduler (carries stop_sessions[], commit_sessions[])
  │
  ├─ Connect (Dashboard)
  │   ├─ GET /jobs/{id}/connect ──► Scheduler (issues ephemeral password)
  │   └─ SSH to Gateway ──► Validates password ──► SSH to container ──► Proxy shell/exec/SFTP
  │
  └─ Commit (Dashboard)
      ├─ POST /jobs/{id}/commit ──► Scheduler (STOPPING flag via heartbeat)
      └─ Worker: docker commit + push ──► POST /commit_complete
```
