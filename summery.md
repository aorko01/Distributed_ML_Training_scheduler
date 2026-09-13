# Distributed ML Training Scheduler — Summary

A distributed platform to submit, build, schedule, and monitor GPU ML jobs across many workers.

**Flow:** User uploads zipped code (`requirements.txt` required) → stored in `uploads` bucket → Builder builds `{user}/{job_id}:latest` and pushes to Docker Hub → VRAM estimation → scheduled to best-fit GPU worker → training with live logs and checkpoint uploads → `COMPLETED / FAILED / RETRY_NEEDED`.

## Components

**1. Scheduler `Scheduler/` (`:8000`)**
Central FastAPI orchestrator with Postgres + Redis.
- Job lifecycle with priorities `NORMAL / REQUESTED / HIGH` and pull-based scheduling: estimation job to max-free-VRAM worker, then retries, then largest-fitting training job (+1GB buffer).
- Stall watchdog requeues jobs if worker heartbeat lost >3min, tracks `gpu_hour` per user.
- Worker registry, cluster overview/throughput stats, resource marketplace, Redis-stream live logs (`GET` + `WS /jobs/{id}/logs/stream`), JWT auth, PyTorch tags helper.

**2. Docker Image Builder `Docker_Image_Builder/`**
Polls `unbuilt_jobs`, builds training images (`FROM base + COPY + pip install + CMD`) and interactive SSH sandboxes (Tailscale), pushes to Docker Hub, streams logs, saves `build.log`.

**3. Object Store `Object_store/` (`:8010`, MinIO `:9000/:9001`)**
S3-compatible storage for code (`uploads`) and outputs/logs (`outputs`). Supports direct upload/download and presigned URLs for large files.

**4. Worker `Worker/` (`:8600` telemetry)**
GPU host agent that registers, heartbeats every 5s, polls jobs every 10s.
- Hardware probing (GPU/VRAM/CPU/RAM/disk), persistent ID, crash resume via `running_job.json`.
- Runs VRAM probe (peak memory + step time), training in Docker `--gpus all` with output monitoring and checkpoint resume.
- Embedded API for live metrics, config edit, pause/resume.

**5. UIs `UI/`**
- **User:** submit jobs, track my jobs/GPU hours, live logs, machines, profile.
- **Admin:** cluster overview, nodes, job queue, throughput charts, users.
- **Worker (Electron desktop):** GPU/resource gauges, jobs table, logs, config, pause/resume.

## Stack & Run
`FastAPI, Postgres, Redis, Docker, MinIO, React+Vite, Electron` + Docker Hub, Tailscale SSH.
- Scheduler: `docker compose up` → `:8000/docs`
- Object Store: `docker compose up` → `:8010`
- Builder: `docker compose up`
- Worker: `pip install -r requirements.txt && python main.py`
- Each UI: `npm install && npm run dev`
