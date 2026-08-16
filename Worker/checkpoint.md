# GPU Job Checkpoint / Restore

## What this is

A best-effort fallback layer that lets a GPU training job running in a Docker
container be **paused and resumed** without the user writing any checkpointing
code. It is **not** the primary fault-recovery mechanism — it's a safety net.

## How it works

1. **Pause a job**
   - `cuda-checkpoint suspend` suspends CUDA and evicts GPU memory to host RAM.
   - `docker checkpoint create` (CRIU) freezes and dumps the whole container to
     `checkpoints/<job_id>/`.
2. **Resume a job**
   - `docker start --checkpoint <name> <container>` restores the container.
   - `cuda-checkpoint resume` reloads the GPU state and training continues.

Everything runs in the background; the worker keeps streaming job logs across
pause/restore cycles so the UI never sees a gap.

## What was implemented

| Component | Purpose |
| --- | --- |
| `checkpoint.py` | Checkpoint manager: cuda-checkpoint suspend/resume, docker checkpoint create/start, GPU validation, periodic snapshots |
| `job_registry.py` | Tracks each running job and its state (`running → checkpointing → paused → restoring`) |
| `executor.py` | Training containers now run detached/named so they can be frozen; log streaming survives stop/restart cycles |
| `server.py` | Control endpoints (below) + `checkpointIntervalSec` in `/api/config` |
| `main.py` | Periodic auto-snapshot loop |
| `config.py` / `runtime_config.py` | Checkpoint settings + tunable interval |

## API

- `POST /api/checkpoint/<job_id>` — pause + snapshot a job
- `POST /api/checkpoint/<job_id>/restore` — restore a paused job
- `GET /api/checkpoint` — overview of checkpointing support + running jobs
- `GET /api/checkpoint/<job_id>` — checkpoint info for a job
- `POST /api/control/pause` — also snapshots **all** running jobs
- `POST /api/control/resume` — also restores all paused jobs
- `PUT /api/config {"checkpointIntervalSec": N}` — tune the auto-snapshot interval

## Tunable auto-snapshot interval

Every N seconds each running job is automatically snapshotted via a
stop-restart cycle (checkpoint, then immediately restart from the fresh
snapshot). `0` disables periodic snapshots; manual endpoints still work.

- Env var: `CHECKPOINT_INTERVAL` (seconds)
- Runtime: `PUT /api/config {"checkpointIntervalSec": N}`
- Per-job override: honored if the scheduler sends `checkpoint_interval` in the
  job payload

## Configuration

| Env var | Default | Meaning |
| --- | --- | --- |
| `CHECKPOINT_ENABLED` | `1` | Master switch; `0` runs jobs exactly as before (`docker run --rm`) |
| `CHECKPOINT_INTERVAL` | `0` | Seconds between auto-snapshots per running job (`0` = off, tunable) |
| `CHECKPOINT_MODE` | `stop_restart` | `stop_restart` leaves container stopped after snapshot; `leave_running` keeps it running with CUDA suspended |
| `CUDA_CHECKPOINT_BIN` | `cuda-checkpoint` | Path to the NVIDIA cuda-checkpoint binary |
| `CHECKPOINT_DIR` | `./checkpoints` | Where snapshots are stored, per job at `checkpoints/<job_id>/` |

## Constraints / limitations

- **Same GPU model + driver version required on restore.** Restore is refused
  if the checkpoint's recorded GPU fingerprint (`meta.json`) doesn't match the
  current host.
- **Single-GPU jobs are reliable.** Multi-GPU jobs on the same node are
  coordinated (all containers frozen before any dump, because NCCL state
  doesn't restore cleanly on its own). Multi-node jobs need an external
  coordinator and are not implemented.
- Requires `cuda-checkpoint` and a Docker daemon running with CRIU /
  experimental features enabled. Missing pieces are detected and skipped
  gracefully, but a snapshot can't actually be taken without them.
- Treated as a **best-effort fallback**, not the primary fault-recovery
  mechanism.
