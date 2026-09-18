# Joining a new GPU worker to the Scheduler

This guide sets up a **worker-only** machine: it runs nothing but `Worker/`
(plus its `Access_Container/` dependency) and registers against an existing
Scheduler. Authority for all paths and secrets is `env.md` §3; this file is
the executable checklist distilled from it.

## 0. What lives on this machine (and what must not)

| On this machine | Never on this machine |
|---|---|
| `Worker/.env` (staging), `/etc/dml/worker.env` (live) | Scheduler `.env`, database secrets, JWT secret |
| Own worker UUID + own worker secret | Other workers' secrets, the full `worker-credentials.json` map |
| Pull-only registry credential (interactive stage only) | Headscale admin key, gateway/bootstrap/controller/builder secrets |

## 1. Prerequisites

- Ubuntu with Docker Engine, the **nvidia** container runtime, and
  `/var/run/docker.sock` accessible (`docker info` must work).
  The worker preflight requires the `overlay2` driver and the `nvidia` runtime.
- Python 3, `python3-venv`, `openssl`, `systemd`.
- A checkout containing at minimum: `Worker/`, `Access_Container/`
  (imported by `Worker/interactive/` at startup via the repo root on
  `sys.path` — a `Worker/`-only copy **crash-loops** with
  `ModuleNotFoundError: No module named 'Access_Container'`),
  and `deploy/interactive/worker/` (the systemd units).

## 2. Configure `Worker/.env`

```bash
cd <checkout>
cp Worker/.env.example Worker/.env
```

Edit `Worker/.env` (all HTTPS URLs must be routable **from this machine**;
a container's `localhost` is not this host):

```dotenv
SCHEDULER_URL=https://scheduler.<yourdomain>          # required, https only
OBJECT_STORE_URL=https://object.<yourdomain>          # required for batch jobs
OBJECT_OUTPUT_BUCKET=outputs
CONTAINER_AS_ROOT=0                                    # recommended
WORKER_SERVICE_CREDENTIAL_FILE=/etc/dml/worker-service.secret
WORKER_ID_FILE=/var/lib/dml-worker/worker-id
WORKER_STATE_DIR=/var/lib/dml-worker
WORKER_API_HOST=127.0.0.1
WORKER_API_PORT=8600
WORKER_ASSIGNMENT_LEASE_SECONDS=45
MAX_CONCURRENT_JOBS=2
DOCKER_DATA_ROOT=<see below>
INTERACTIVE_WORKER_ENABLED=0                           # keep 0 until interactive acceptance
```

Fill `DOCKER_DATA_ROOT` with this machine's **actual** Docker data root,
not the default:

```bash
cat /etc/docker/daemon.json   # if "data-root" is set, use that value
```

If `daemon.json` has no `data-root`, use `/var/lib/docker`.
(`hardware.py` uses this for disk telemetry; a wrong value only skews
reported disk space, but set it correctly anyway.)

`WORKER_SCHEDULER_CA_FILE` is needed **only** for a private CA; with a
public CA (e.g. Let's Encrypt) leave it absent — the worker then uses the
system trust store. Verify trust with:

```bash
curl -sS -o /dev/null -w "http=%{http_code} tls=%{ssl_verify_result}\n" https://scheduler.<yourdomain>/
```

`tls=0` means the certificate verifies (`404` on `/` is normal).

## 3. Stage the production env file and directories

```bash
sudo install -o root -g root -m 0700 -d /etc/dml /var/lib/dml-worker
sudo cp Worker/.env /etc/dml/worker.env
sudo chmod 0600 /etc/dml/worker.env
```

`dml-worker.service` reads **only** `/etc/dml/worker.env`
(`PYTHON_DOTENV_DISABLED=1`); re-copy after every `.env` change and
`systemctl restart dml-worker`.

## 4. Provision the worker identity set (one UUID + one secret)

Do these three as **one set** before first start — a `.env` alone is not
sufficient (`env.md` §3):

```bash
# 1. worker UUID (this machine)
python3 -c "import uuid; print(uuid.uuid4(), end='')" | sudo tee /var/lib/dml-worker/worker-id >/dev/null

# 2. matching secret (this machine only, 0600, NO trailing newline)
sudo openssl rand -hex 32 | tr -d '\n' | sudo tee /etc/dml/worker-service.secret >/dev/null
sudo chown root:root /var/lib/dml-worker/worker-id /etc/dml/worker-service.secret
sudo chmod 0600 /var/lib/dml-worker/worker-id /etc/dml/worker-service.secret

# 3. on the SCHEDULER VM, add the pair to /etc/dml/worker-credentials.json:
#      {"<worker-uuid>": ["<worker-secret>"]}
```

Secret rules (enforced by `scheduler_protocol.py`, startup fails otherwise):
regular file (no symlink), no group/other permission bits, 32–256 chars,
≥8 distinct characters, no trailing newline. Verify:

```bash
stat -c "%a %F" /etc/dml/worker-service.secret   # want: 600 regular file
sudo tail -c 1 /etc/dml/worker-service.secret | xxd  # must NOT end with 0a
```

## 5. Build the `/opt/dml` layout

The units expect exactly this layout — do **not** point them at the checkout:

```bash
cd <checkout>
sudo mkdir -p /opt/dml
sudo cp -a Worker /opt/dml/Worker
sudo cp -a Access_Container /opt/dml/Access_Container
# drop venv/caches/logs and the staging .env (the unit must not see it):
sudo rm -rf /opt/dml/Worker/venv /opt/dml/Worker/__pycache__ \
  /opt/dml/Worker/.pytest_cache /opt/dml/Worker/output \
  /opt/dml/Worker/worker.log /opt/dml/Worker/.env
sudo python3 -m venv /opt/dml/venv
sudo /opt/dml/venv/bin/pip install -r /opt/dml/Worker/requirements.txt
```

Do not reuse a venv copied from another path — its paths are baked in.

## 6. Install, enable, start the units

```bash
sudo cp deploy/interactive/worker/dml-worker.service \
        deploy/interactive/worker/dml-worker-lease-guard.service \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dml-worker-lease-guard.service dml-worker.service
```

`dml-worker.service` requires the guard and restarts on failure, so the
worker survives SSH logout.

## 7. Verify (worker acceptance, Flag 1)

On this machine:

```bash
systemctl status dml-worker.service dml-worker-lease-guard.service --no-pager
journalctl -u dml-worker.service --since "10 min ago" --no-pager | tail -20
curl -s http://127.0.0.1:8600/api/status
# → {"connected":true,"lastHeartbeatAt":"<time>",...}
curl -s http://127.0.0.1:8600/api/events | python3 -m json.tool | tail -25
# → steady "Heartbeat sent" entries every ~5s
```

A quiet journal is **normal** for an idle worker: it logs startup, claims,
and errors only. Heartbeat flow is observed via `/api/status`
(`connected:true`, fresh `lastHeartbeatAt`) and `/api/events`.

On the Scheduler VM: confirm the Worker row, steady heartbeat arrivals,
and lease renewals in the API logs. Then complete **one real batch job**
on this worker. Only after that does the Scheduler operator flip
`WORKER_NEW_WORK_ENABLED` to `1` in `Scheduler/.env.runtime`
(`env.md`: "change to 1 only after worker acceptance").
Keep `INTERACTIVE_WORKER_ENABLED=0` here until the interactive acceptance
chain (builder, management/gateway, Headscale, HTTPS proxy, real-browser
WebSocket test) has passed.

## 8. Troubleshooting

| Symptom | Cause → fix |
|---|---|
| `Scheduler ... (401)` | Secret mismatch: the secret in `/etc/dml/worker-service.secret` differs from the Scheduler map entry (check trailing newline, re-copy the exact bytes). |
| `Scheduler ... (503)` | Scheduler has no usable credentials map: fix `/etc/dml/worker-credentials.json` on the Scheduler VM and recreate its API container. No change here will help. |
| `Scheduler ... (404)` right after a crash/reinstall | Orphaned `claim` token in `<state-dir>/assignments.sqlite` replays before `register()` and hits "Worker not registered". Only if the journal is empty (no persisted assignments): stop the worker, back up the sqlite file, `DELETE FROM metadata WHERE key='claim';`, restart. |
| `ModuleNotFoundError: No module named 'Access_Container'` | `/opt/dml/Access_Container` missing — copy it from the checkout (§5) and restart. |
| `Cannot create state dir ... permission denied` | Running as non-root against `/var/lib/dml-worker`. Either run the production install (§3–§6) or run dev-mode (§9). |
| Crash loop after copying a venv | Rebuild it in place (§5): venvs are path-bound. |
| `Worker Scheduler HTTPS required` | `SCHEDULER_URL` must be `https://`, not `http://`. |

## 9. Dev-mode run (non-root, testing only)

Without sudo, run from the checkout with user-writable paths overriding the
system ones (the worker never writes the fallback into `.env`):

```bash
cd Worker
export WORKER_STATE_DIR="$HOME/.local/share/dml-worker"
export WORKER_ID_FILE="$HOME/.local/share/dml-worker/worker-id"
export WORKER_SERVICE_CREDENTIAL_FILE="$HOME/.config/dml/worker-service.secret"
./venv/bin/python main.py          # or: bash run.sh
```

Keep it under `tmux`/a supervisor so it survives logout, and stop it
before starting the production units to avoid double-claiming. The
Scheduler pairing (§4 step 3) is still required — use the same UUID/secret
pair in both modes, never two different pairs for one worker id.
