# Worker Agent UI

Cross-platform desktop app (Electron + React + TypeScript) for the Distributed ML worker agent.

> **Status: dummy UI** — all data is simulated locally in the renderer. It does **not** talk to the
> Python worker process (`Worker/main.py`) or the scheduler yet. The Disconnect/Reconnect controls
> only toggle local UI state.

## Features

- **Worker information**: worker ID, hostname, IP, OS, platform, scheduler URL, heartbeat / job-poll intervals
- **GPU card**: GPU model, CUDA/Docker availability, GPU load, VRAM used/free, temperature
- **Resource utilization**: live-simulated CPU, memory, GPU load, and VRAM gauges plus a CPU history sparkline
- **Disconnect / Reconnect**: confirm-and-disconnect modal that simulates the worker going offline
  (metrics idle out, jobs/log stream pause)
- **Logs console**: streaming dummy worker logs with pause / clear / export to file
- **Dark UI** consistent with the existing Admin dashboard

## Requirements

- **Node.js >= 22.12** (and npm). Install from https://nodejs.org or via `nvm install 22`.
- The app itself is cross-platform (Windows / macOS / Linux). GPU/CUDA/Docker are **not** required —
  nothing is actually probed.

## Getting started

```bash
npm install
npm run dev
```

`npm run dev` launches the Electron window with Vite hot-reload.

## Scripts

| Command             | Description                                  |
| ------------------- | -------------------------------------------- |
| `npm run dev`       | Run the app in development (HMR)             |
| `npm start`         | Preview the production build                 |
| `npm run build`     | Typecheck + build main/preload/renderer      |
| `npm run typecheck` | Type-check main, preload and renderer        |
| `npm run lint`      | Lint with oxlint                             |

## Project structure

```
UI/Worker/
├── electron.vite.config.ts   # electron-vite config
├── package.json
├── src/
│   ├── main/index.ts         # Electron main process (window creation)
│   ├── preload/index.ts      # Preload bridge (exposes platform/versions only)
│   └── renderer/
│       ├── index.html
│       └── src/
│           ├── App.tsx              # view switching + connection state
│           ├── index.css
│           ├── data/mock.ts         # all dummy worker data
│           ├── hooks/useSimulation.ts   # simulated live metrics
│           ├── components/          # Sidebar, cards, gauges, modal, tables
│           └── views/               # Dashboard, Logs
```

## Next steps (real integration)

When wiring the real worker:

1. Expose IPC channels in `src/main/index.ts` and `src/preload/index.ts` (e.g. `worker:metrics`,
   `worker:disconnect`, `worker:reconnect`) talking to the worker process or its HTTP API.
2. Replace `data/mock.ts` and `hooks/useSimulation.ts` with data sourced from the worker's heartbeat payload
   (see `Worker/hardware.py` — `collect_node_info()`, `get_gpu_info()`).
3. Use `window.worker` in `src/preload/index.d.ts` to type the bridge.
