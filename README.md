# Distributed ML Training Scheduler
This project schedules GPU training workloads across worker machines. It is built as a multi-service system:
- `Scheduler` (FastAPI + Postgres + Redis): accepts jobs, tracks worker state, and assigns runnable jobs.
- `Docker_Image_Builder`: watches uploaded job folders, builds Docker images, pushes them to Docker Hub, and marks jobs as pending.
- `Worker`: registers with the scheduler, sends heartbeats, pulls jobs, runs containers on GPU, and uploads output logs.

## High-level workflow
1. A client uploads a zip job via `Scheduler` (`/jobs/submit_job`) with:
   - zipped project files
   - `entry_file` (script to run)
   - optional `vram_required`
2. Scheduler stores the extracted files under `Scheduler/uploads/<job_id>/` and creates a DB row with status `NOT_RUNNABLE`.
3. `Docker_Image_Builder` detects the uploaded project, builds and pushes image `<docker_user>/<job_id>:latest`, then calls `/jobs/update_job_to_pending`.
4. Worker polls `/jobs/pull_job` with free VRAM and receives the oldest compatible `PENDING` job.
5. Worker pulls image, runs `python <entry_file>` in Docker with GPU access, writes logs to `Worker/output/<job_id>.txt`, and uploads that file via `/jobs/upload_output`.
6. Scheduler marks job as `COMPLETED`.

## Repository layout
```text
Distributed_ML_Training_scheduler/
├── Scheduler/
│   ├── app/
│   │   ├── api/            # FastAPI routes
│   │   ├── services/       # job/worker service logic
│   │   ├── models/         # SQLAlchemy models
│   │   ├── schemas/        # Pydantic request/response schemas
│   │   ├── core/redis.py   # Redis client
│   │   └── db/database.py  # DB engine/session setup
│   ├── docker-compose.yml  # postgres + redis + api
│   └── requirements.txt
├── Docker_Image_Builder/
│   ├── builder.py          # poll uploads -> build/push image -> notify scheduler
│   ├── Common_image.py     # prebuild/push reusable ML base images
│   ├── docker-compose.yml
│   └── requirements.txt
├── Worker/
│   ├── worker.py           # register, heartbeat, pull, run, upload logs
│   ├── pull_common_image.py
│   └── requirements.txt
└── README.md
```

## Tech stack
- Python 3.10/3.11
- FastAPI + Uvicorn
- SQLAlchemy + PostgreSQL
- Redis (for heartbeat presence + TTL)
- Docker SDK for Python
- GPU inspection: `GPUtil`

## Prerequisites
- Docker + Docker Compose
- Access to a Docker Hub account (for pushed training images)
- NVIDIA GPU runtime support on worker host (for GPU container execution)

## Setup
### 1) Start Scheduler stack
From `Scheduler/`:
```bash
docker compose up --build
```
This starts:
- Postgres on `5432`
- Redis on `6379`
- FastAPI API on `8000` (default)

### 2) Configure and start Image Builder
Create `Docker_Image_Builder/.env`:
```env
DOCKER_HUB_USERNAME=your_dockerhub_username
DOCKER_HUB_PASSWORD=your_dockerhub_password
SCHEDULER_API_URL=http://localhost:8000
UPLOADS_DIR=/uploads
POLL_INTERVAL=10
```

Then run from `Docker_Image_Builder/`:
```bash
docker compose up --build
```

Notes:
- The compose file mounts `../Scheduler/uploads` into `/uploads` (read-only).
- It also mounts Docker socket so this service can build/push images.

### 3) Configure and start Worker
Create `Worker/.env`:
```env
SCHEDULER_URL=http://localhost:8000
```

Install dependencies and run:
```bash
cd Worker
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python worker.py
```

Worker behavior:
- Generates/persists `worker_id` in `worker_id.txt`.
- Registers once, then heartbeat every 5s.
- Polls for jobs every 10s.
- Runs up to `MAX_CONCURRENT_JOBS` (currently `4`) jobs in parallel.

## API endpoints (Scheduler)
Base URL: `http://localhost:8000`

### Worker endpoints
- `POST /workers/register`
  - Body:
    - `worker_id`
    - `mac_address`
    - `gpu_type`
    - `num_gpus`
    - `total_vram`
- `POST /workers/heartbeat`
  - Body:
    - `worker_id`
    - `gpu_type`
    - `available_vram`

### Job endpoints
- `POST /jobs/submit_job` (multipart)
  - Form fields:
    - `zip_file` (required)
    - `entry_file` (required; filename to run, e.g. `train.py`)
    - `vram_required` (optional, float GB)
- `POST /jobs/update_job_to_pending`
  - JSON body: `{ "job_id": "<uuid>" }`
- `POST /jobs/pull_job`
  - JSON body:
    - `worker_id`
    - `gpu_type`
    - `free_vram`
- `POST /jobs/upload_output` (multipart)
  - Expects output file named `<job_id>.txt`

## Job package format
Your uploaded ZIP should contain at least:
- `requirements.txt` (required by current pipeline)
- entry script file referenced by `entry_file`

Example:
```text
my_training_project.zip
└── my_training_project/
    ├── requirements.txt
    ├── train.py
    └── other_modules.py
```

## Base image selection strategy (Image Builder)
`Docker_Image_Builder/builder.py` inspects `requirements.txt` and chooses a base image:
- Transformers-related packages -> `ml-base-transformers`
- Vision-related packages -> `ml-base-vision`
- Training tooling -> `ml-base-training`
- fallback -> `pytorch/pytorch:2.1.0-cuda11.8-cudnn8-runtime`

Image tag pattern:
- `<DOCKER_HUB_USERNAME>/<job_id>:latest`

## Optional: prebuild and push shared base images
`Docker_Image_Builder/Common_image.py` can build and push reusable base images:
- `ml-base`
- `ml-base-transformers`
- `ml-base-vision`
- `ml-base-training`

Run (after setting Docker Hub credentials):
```bash
cd Docker_Image_Builder
python Common_image.py
```

Workers can pre-pull these:
```bash
cd Worker
DOCKER_HUB_USERNAME=your_dockerhub_username python pull_common_image.py
```

## Operational notes
- Uploaded job files are stored in `Scheduler/uploads/`.
- Output logs are stored in:
  - `Worker/output/` (worker-side)
  - `Scheduler/output/` (after upload)
- Job lifecycle states in DB:
  - `NOT_RUNNABLE` -> `PENDING` -> `IN_PROGRESS` -> `COMPLETED`
- Worker heartbeat data is cached in Redis with TTL (`HEARTBEAT_TTL = 15`).

## Troubleshooting
- **`zsh: command not found: add`**
  - `add ...` is not a shell command. Use plain English with the assistant, or run real shell commands like `git add`, `docker compose up`, etc.
- **Worker cannot register**
  - Check `Worker/.env` (`SCHEDULER_URL`) and confirm scheduler API is reachable.
- **Image pull/build failures**
  - Verify Docker Hub credentials and image visibility.
  - Ensure `DOCKER_HUB_USERNAME` matches image tags produced by builder.
- **No jobs pulled**
  - Ensure jobs were moved to `PENDING` (builder must call `/jobs/update_job_to_pending`).
  - Ensure worker has enough free VRAM for `vram_required`.

## Next improvements (recommended)
- Move `DOCKER_HUB_USERNAME` in `Worker/worker.py` to environment variable.
- Add auth + job ownership checks on Scheduler endpoints.
- Add retries/backoff for network calls and image operations.
- Add tests and CI for API and worker flows.