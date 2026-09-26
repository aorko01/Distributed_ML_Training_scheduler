# DML Worker Console

Linux Electron console for the local Distributed ML Worker. The Python Worker
runs independently under `dml-worker.service`; closing this window never stops
heartbeats, assignments, or containers.

The console reads the loopback-only API at `http://127.0.0.1:8600` and shows:

- CPU, memory, Docker-storage, disk/network I/O, host identity, and uptime.
- Every detected NVIDIA GPU, including utilization, VRAM, and temperature.
- Live coordinator assignments plus recent completed/failed jobs.
- Worker Python logs captured in-process. Docker/container logs are deliberately
  not read or displayed.
- Scheduler connectivity and the Worker's current assignment mode.

The **Accept no more jobs** control stops new assignments through the local
Worker API. The setting survives a Worker restart; active jobs continue to run.

## Install the complete application

From the repository root on Ubuntu:

```bash
sudo bash install.sh
```

That command installs the Worker and lease guard as systemd services, builds
the Electron console, creates the `dml-worker-ui` launcher and desktop-menu
entry, detects Docker's active data root, and performs the GPU/storage checks.
See `Worker/setup_worker.md` for registration details.

## Develop the console

Node.js 22 or newer is required:

```bash
npm ci
npm run dev
```

Useful checks:

```bash
npm run typecheck
npm run lint
npm run build
```

The renderer talks directly to the loopback API. The preload exposes only the
platform, Electron versions, and API URL; it does not control the systemd
service or provide shell access.
