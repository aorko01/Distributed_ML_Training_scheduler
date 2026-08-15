# Worker Features

## Overview
This Worker component is a distributed machine learning training worker that executes jobs on GPU-equipped machines. It communicates with a central scheduler to pull and execute training jobs, monitors system resources, and manages containerized job execution.

## Key Components

### 1. Scheduler Communication (`api.py`)
- **Worker Registration**: Registers with scheduler providing GPU specs and node info
- **Heartbeat Monitoring**: Periodic reporting of GPU status and node metrics
- **Job Management**: Pulls jobs based on available VRAM, marks completion
- **Log Streaming**: Real-time log streaming for UI display
- **VRAM Estimation**: Saves VRAM usage data for capacity planning

### 2. Configuration Management (`config.py`)
- Scheduler URLs and endpoints
- Object store configuration
- Execution intervals (heartbeat: 5s, job poll: 10s)
- File paths and directory setup
- Docker hub credentials

### 3. Hardware Monitoring (`hardware.py`)
- **GPU Info**: GPU name, VRAM total/free, GPU count, load
- **System Metrics**: CPU load, memory usage
- **Network**: Hostname and IP address detection
- **GPU Usage**: Tracks in-use GPU count
- **Persistent ID**: Generates and stores worker ID

### 4. Job Execution (`executor.py`)
- **Docker Integration**: Pulls images and runs containers with GPUs
- **VRAM Estimation**: Runs preliminary VRAM usage tests
- **Training Execution**: Handles job execution with log management
- **Log Management**: Uploads and streams logs at configured intervals
- **Build Logs**: Maintains persistent build logs for debugging

### 5. Output Management (`output_monitor.py`)
- Monitors job output directories
- Uploads new/changed files to object store
- Uses file modification timestamps for change detection

### 6. Object Storage (`object_store.py`)
- Uploads files and bytes to object store
- Downloads files from object store
- Used for storing job outputs and build logs

### 7. VRAM Estimation (`vram_estimation.py`)
- Probes model VRAM usage during training
- Measures peak reserved memory and step times
- Used for capacity planning and job scheduling

### 8. Worker Entry Point (`main.py`)
- Main worker process that:
  - Registers with scheduler
  - Runs heartbeat thread
  - Runs job processing thread
  - Handles graceful shutdown

## Execution Flow
1. Worker starts and registers with scheduler
2. Heartbeat thread continuously reports GPU status
3. Job thread polls for jobs based on available VRAM
4. When a job is received:
   - Pull Docker image
   - Either run VRAM estimation or training
   - Monitor and upload outputs
   - Stream logs to scheduler
   - Mark job as completed when done

## Configuration
Set `SCHEDULER_URL` environment variable. Optional:
- `OBJECT_STORE_URL`: Object store endpoint
- `OBJECT_OUTPUT_BUCKET`: Output bucket name
- `DOCKER_HUB_USERNAME`: Docker registry credentials
- Log upload/push intervals

## Requirements
- Python 3.x
- Docker with GPU support
- GPU with CUDA support
- Access to scheduler API
- Object store (e.g., MinIO, S3)