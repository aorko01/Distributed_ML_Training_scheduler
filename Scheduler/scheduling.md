# Scheduler Architectural Overview

## System Architecture

The scheduler is a **pull-based** distributed job scheduling system for ML training jobs across GPU workers. Key components:

- **FastAPI Application** (`app/main.py`): Main entry point, initializes routers and lifespan (watchdog task)
- **PostgreSQL**: Persistent job/state storage via SQLAlchemy
- **Redis**: In-memory fast state for heartbeats, job-worker assignments, and interactive session liveness
- **Workers**: Poll `POST /pull_job` to receive assigned jobs; send heartbeats every 15s with resource metrics

---

## Scheduling Strategies

Job assignment occurs when a worker calls `get_next_job_for_worker()`. Strategies are executed in priority order (defined in `app/services/scheduler_service.py:460-465`). The **first strategy that returns a job** wins:

| Strategy | Priority | Condition |
|---|---|---|
| **Interactive** | 0 | Worker idle (no batch job running); dispatches `INTERACTIVE_READY` jobs using two-container model (env + access image) |
| **VRAM Estimation** | 1 | Worker has highest VRAM among connected workers; checks for `VRAM_ESTIMATION_PENDING` jobs |
| **Retry** | 3 | Job status `RETRY_NEEDED` (infra failure); only if worker's free VRAM fits the job's requirement |
| **Training** | 4 | Job status `RUNNABLE` where `(vram_required + 1.0) <= available_vram`; selects job with **largest** `vram_required` first |

**VRAM Allocation Rule**: `(job.vram_required + 1.0) <= worker.available_vram` — the `+1.0` GB provides a safety margin.

---

## Worker Heartbeat & Liveness

- Workers send heartbeats every **15 seconds** via `HeartbeatSchema` to `POST /heartbeat`
- Redis keys: `worker:<worker_id>` — holds current `available_vram`, `gpu_load`, `gpu_type`, `gpus_in_use`, etc.
- TTL: 15s (refreshed on every heartbeat)
- Stale workers (no heartbeat for 3 min) → assigned jobs marked `RETRY_NEEDED` by the **stall watchdog**

---

## Interactive Session Lifecycle

Interactive sessions use a **two-container model**:

1. **env_image**: Training environment (derived from base job or freshly built)
2. **access_image**: Shared `aorko123/access-sshd:latest` providing SSH + Tailnet access

**States**: `PENDING → DEPLOYING → RUNNING → STOPPING → STOPPED`

- Created via `interactive_service.create_session()` → `PENDING` state
- Bound to a worker via `_check_interactive_job_strategy()` → `DEPLOYING`
- Liveness tracked via Redis `interactive_heartbeat:<session_id>` (TTL: 90s)
- Watchdog requeues stalled sessions (→ `PENDING`, job → `INTERACTIVE_READY`) or finalizes stopping sessions

---

## Job State Machine

| Status | Meaning | Transitions |
|---|---|---|
| `NOT_RUNNABLE` | Waiting to be submitted/initialized | → `VRAM_ESTIMATION_PENDING` (after VRAM estimation) |
| `VRAM_ESTIMATION_PENDING` | VRAM being estimated | → `RUNNABLE` (after estimation) |
| `RUNNABLE` | Ready to be scheduled | → `IN_PROGRESS` (when pulled by worker) |
| `IN_PROGRESS` | Actively running on a worker | → `COMPLETED`, `FAILED`, or `RETRY_NEEDED` (on stall) |
| `COMPLETED` | Job finished successfully | terminal |
| `FAILED` | User code error | terminal |
| `RETRY_NEEDED` | Infrastructure issue; requeued later | → `RUNNABLE` (when worker retries) |
| `INTERACTIVE_READY` | Interactive session ready for dispatch | → `INTERACTIVE_DEPLOYING` (when worker pulls) |
| `INTERACTIVE_DEPLOYING` | Builder deploying containers | → `INTERACTIVE_RUNNING` or requeued on stall |
| `INTERACTIVE_RUNNING` | SSH session active | → `INTERACTIVE_STOPPED` |
| `INTERACTIVE_STOPPED` | Session terminated | terminal |

---

## Pull Flow (Worker → Scheduler)

```text
Worker calls: POST /pull_job {worker_id, gpu_type, free_vram, ...}

1. get_next_job_for_worker():
   - Reject if worker is in testing mode
   - Reject if worker has active interactive session (DEPLOYING/RUNNING/STOPPING)
   - Iterate SCHEDULING_STRATEGIES in order:
     * Interactive strategy → if worker idle + INTERACTIVE_READY job found
     * VRAM estimation strategy → if worker has highest VRAM + VRAM_ESTIMATION_PENDING job
     * Retry strategy → if job RETRY_NEEDED + VRAM fits
     * Training strategy → if RUNNABLE job fits VRAM (largest first)
   - Return first matched job or None

2. If job found:
   - Job status → `IN_PROGRESS` (or `INTERACTIVE_DEPLOYING`)
   - `started_at` = now
   - `device` = worker's gpu_type
   - Redis: `job_worker:<job_id>` = worker_id
   - Return job info to worker (env image tag, access image, credentials)
```

---

## Stall Watchdog

Runs every **30 seconds** (`run_stall_watcher()` in `watchdog_service.py`):

- **check_stalled_jobs()**: Scans all `IN_PROGRESS`/`VRAM_ESTIMATION_PENDING` jobs; if worker heartbeat older than 3 min → mark job `RETRY_NEEDED`, clean Redis mapping
- **check_stalled_interactive_sessions()**: Scans `DEPLOYING`/`RUNNING`/`STOPPING` sessions; if worker or container stale > 90s → finalize (STOPPED) or requeue (→ PENDING, INTERACTIVE_READY)

---

## Resource Overview

Admin endpoints query cluster state:

- `GET /overview` → `scheduler_service.get_overview()`: Online nodes, total GPUs, cluster avg GPU load, queue depth, GPU allocation
- `GET /throughput` → `scheduler_service.get_throughput()`: Completed-job counts bucketed by daily/weekly/monthly/yearly
- `GET /queue_length` → Number of `RUNNABLE` jobs