# Joining a new GPU worker to the Scheduler

Run these on the **new worker machine**. You only need this repo checkout and
`sudo`. The installer does everything else.

## 1. Prerequisites

- Ubuntu with an NVIDIA driver installed (reboot after installing it).
- A checkout of this repo (needs `Worker/`, `Access_Container/`,
  `deploy/interactive/worker/`).

## 2. Fill in `Worker/.env`

```bash
cd <checkout>
cp Worker/.env.example Worker/.env
```

Edit `Worker/.env` — set these, leave the rest:

```dotenv
SCHEDULER_URL=https://scheduler.zulfiker.xyz
OBJECT_STORE_URL=https://object.zulfiker.xyz
INTERACTIVE_WORKER_ENABLED=1
INTERACTIVE_REGISTRY_PREFIXES=docker.io/aorko123
INTERACTIVE_ACCESS_IMAGE=docker.io/aorko123/access@sha256:5be114c0b1564ff18eece843bce01409e66275a6def674b4d1c9f420a455ec4e
INTERACTIVE_PREFLIGHT_IMAGE=docker.io/aorko123/quota-fixture@sha256:b0b2526e7fe571f62b3077ff32cf0371182348f400714bcc0c561bda70dd81c3
```

You do **not** need to set `DOCKER_DATA_ROOT` — the installer detects Docker's
real data root itself and writes it into the live config.

## 3. Run the installer

```bash
sudo bash Worker/join_worker.sh
```

This installs Docker + NVIDIA toolkit if missing, creates this worker's UUID
and secret, deploys everything under `/opt/dml`, installs the systemd units,
and pre-pulls the service images. You do **not** create the UUID/secret
yourself — the installer generates both.

## 4. Register on the scheduler (manual step)

The installer prints a JSON entry like:

```json
{"<worker-uuid>": ["<worker-secret>"]}
```

There is no self-registration: merge that entry into
`/etc/dml/worker-credentials.json` **on the scheduler VM**, then recreate or
restart the scheduler API so it reloads the file. Back on the worker, type
`REGISTERED` to start.

Need the entry again later? Run:

```bash
sudo bash Worker/join_worker.sh --show-registration
```

## 5. Verify

```bash
systemctl is-active dml-worker
curl -s http://127.0.0.1:8600/api/status   # want connected:true, fresh lastHeartbeatAt
```

After any later `.env` change, just rerun the installer — it re-stages the
live config (`/etc/dml/worker.env`, the only file the service reads) and
restarts the worker:

```bash
sudo bash Worker/join_worker.sh --registered
```
