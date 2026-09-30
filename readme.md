# Distributed ML Training Scheduler — Interactive Workspaces

Live deployment. All URLs below are absolute and live.

## Live URLs

- User UI: `https://distributeml.zulfiker.xyz`
- Admin UI: `https://admin.zulfiker.xyz`
- Scheduler API: `https://scheduler.zulfiker.xyz`
- Gateway WSS (via Scheduler host Caddy → `127.0.0.1:8030`): `wss://scheduler.zulfiker.xyz/v1/connect/<resource>/<service>`

## Small architectural overview

```
Browser https://distributeml.zulfiker.xyz ──HTTPS──> Scheduler https://scheduler.zulfiker.xyz
VS Code + dml-ssh ──HTTPS (grant) + WSS (data)──> Scheduler + Gateway wss://scheduler.zulfiker.xyz/v1/connect/…
Scheduler <--management--> Headscale_Management <--Headscale API--> Headscale (internal, no public URL)
Worker host (Ubuntu+Docker, systemd dml-worker) ──heartbeat/claim──> Scheduler
Worker builds: workload (GPU, /workspace, sshd 127.0.0.1:2222) + broker (Unix socket) + Access (relay) + Tailscale sidecar (Serve 127.0.0.1:9000)
Gateway ──tailnet TCP 9000──> Access ──Unix socket──> Broker ──docker-exec / sshd──> workload
```

- Scheduler owns identity, workspaces/revisions/runtimes, `generation` fencing, `ssh_capable/ssh_ready` gates, single-use grants. See `code.md` §2.
- Worker owns 3 labeled `dml-<assignment>` containers, ordered startup, health reporting, exact-ID cleanup. See `Worker/setup_worker.md`, `Worker/interactive/manager.py:Manager.execute()`.
- Management owns enrollment, resource versioning, Ed25519 tickets (`purpose=browser|ssh`). Gateway only does ticket auth + tailnet dial + blind relay.
- SSH reuses the same `workspace` tailnet service/port 9000. Browser (`OPEN`, 30m) and SSH (`SSH_OPEN`, 4h) differ only by grant purpose + first record. No public :22.
- Full file/function order: `code.md`. Full protocol: `interactive.md`. Client runbook: `docs/vscode-remote-ssh-client.md`.

## Instruction for user (browser + dml-ssh / VS Code)

Prereqs on laptop: VS Code + Remote-SSH extension, OpenSSH (`ssh -V`), Python 3.9+.

```bash
# 1. one-time per machine
pip install ./dml-ssh
dml-ssh doctor
dml-ssh login --scheduler https://scheduler.zulfiker.xyz
# expect: logged in (interactive:ssh scope)

# optional shortcut
export DML_SCHEDULER_URL=https://scheduler.zulfiker.xyz
```

1. Open `https://distributeml.zulfiker.xyz`, create workspace (from upload/job), build image, press Start.
2. Wait for `READY`. If `Connect with VS Code` block is missing, runtime is browser-only (old image without `io.dml.vscode-ssh-profile=v1`, still starting, or `sshd-failed`) — rebuild image / wait.
3. Copy-paste the per-runtime command from UI:
```bash
dml-ssh configure <runtime-id> --scheduler https://scheduler.zulfiker.xyz
# first run may print: echo "Include ~/.ssh/dml-config" >> ~/.ssh/config  → run it once
code --folder-uri vscode-remote://ssh-remote+dml-<runtime-id>-g<generation>/workspace
# or: dml-ssh configure <id> --scheduler https://scheduler.zulfiker.xyz --open
```
4. In VS Code: Remote-SSH → host `dml-<id>-g<n>` → Open Folder `/workspace`. Verify:
```bash
pwd; id -u  # want /workspace, 10001
python -c "import torch; print(torch.cuda.is_available())"
```
Everyday: reconnect just works (`ProxyCommand dml-ssh proxy` fetches fresh single-use grant). `scp file dml-<id>-g<n>:/workspace/` works. New runtime → repeat `configure` only. Stop in UI kills SSH by design. Never paste browser JWT into SSH — `dml-ssh login` only. Details: `docs/vscode-remote-ssh-client.md`, `dml-ssh/README.md`.

## Worker setup (install.sh)

Run on new Ubuntu GPU worker (NVIDIA driver + reboot done, XFS+pquota for quotas, checkout with `Worker/`, `Access_Container/`, `deploy/interactive/worker/`).

```bash
cd <checkout>
sudo bash install.sh
# creates Worker/.env (never overwrites) with:
#   SCHEDULER_URL=https://scheduler.zulfiker.xyz
#   INTERACTIVE_ALLOW_SSH=1, INTERACTIVE_SSH_CAPACITY=8, etc.
# prints {"<worker-uuid>": ["<worker-secret>"]}
```

Then on scheduler VM merge that JSON into `/etc/dml/worker-credentials.json`, restart scheduler API, back on worker type `REGISTERED`.

Verify / operate:
```bash
systemctl is-active dml-worker
curl -s http://127.0.0.1:8600/api/status  # want connected:true
dml-worker-ui  # Electron console
sudo bash Worker/join_worker.sh --check
sudo bash Worker/join_worker.sh --show-registration
sudo bash Worker/join_worker.sh --show-runtime-env
# after .env change:
sudo bash Worker/join_worker.sh --registered
# never restart with live runtime — drains assignments, clients get 409
```

SSH flags are live: `INTERACTIVE_ALLOW_SSH=1`, pinned `INTERACTIVE_ACCESS_IMAGE=docker.io/aorko123/access@sha256:…`, scheduler `INTERACTIVE_SSH_ENABLED=1`, gateway `GW_ALLOW_CLI=1`. Old tag `aorko123/access-sshd:latest` must not be used. Full join + SSH verification gates: `Worker/setup_worker.md` §2-§6.5.
