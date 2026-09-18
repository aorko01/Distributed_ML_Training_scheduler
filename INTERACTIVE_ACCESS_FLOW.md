# Interactive Access Flow — Complete Deep Dive

> How a user goes from clicking “New Session” to a root shell in `/workspace`
> that behaves like their own Ubuntu VM with GPUs, and how that shell gets
> committed back into a batch-runnable image and torn down.
>
> Covers: Scheduler, Docker Image Builder, Worker, AccessContainer,
> Gateway, Headscale, Redis, Docker flags, Linux namespaces, SSH proxying,
> environment reconstruction, identity/caches, exec/sftp/port-forwarding,
> timeouts, commit, stop, failures, operator runbook.
>
> Source of truth as of this repo state: two-container model with
> `--privileged` env + `--pid/--network container:<env>` access,
> `enter-env.sh` ForceCommand + `SSH_ORIGINAL_COMMAND` support,
> gateway paramiko proxy with `direct-tcpip`.

---

## Table of Contents

1. [Mental Model in 30 Seconds](#1-mental-model-in-30-seconds)
2. [Actors and Repositories](#2-actors-and-repositories)
3. [End-to-End Sequence](#3-end-to-end-sequence)
4. [Step 1 — Create: POST /interactive/create](#4-step-1--create-post-interactivecreate)
5. [Step 2 — Build: Builder Loop](#5-step-2--build-builder-loop)
6. [Step 3 — Dispatch: pull_job Returns flag=interactive](#6-step-3--dispatch-pull_job-returns-flaginteractive)
7. [Step 4 — Deploy: Worker Starts Two Containers](#7-step-4--deploy-worker-starts-two-containers)
8. [Step 5 — Prepare: _prepare_env_container Makes It VM-like](#8-step-5--prepare-_prepare_env_container-makes-it-vm-like)
9. [Step 6 — Tailnet Join and IP Detection](#9-step-6--tailnet-join-and-ip-detection)
10. [Step 7 — Connect Info and Ephemeral Password](#10-step-7--connect-info-and-ephemeral-password)
11. [Step 8 — Gateway SSH Auth and Proxy](#11-step-8--gateway-ssh-auth-and-proxy)
12. [Step 9 — Access Container: sshd + enter-env.sh + nsenter](#12-step-9--access-container-sshd--enter-envsh--nsenter)
13. [Step 10 — Using the Session Like a VM](#13-step-10--using-the-session-like-a-vm)
14. [Step 11 — Commit: Turning /workspace Into a Batch Image](#14-step-11--commit-turning-workspace-into-a-batch-image)
15. [Step 12 — Stop, Timeouts, Monitor, Cleanup](#15-step-12--stop-timeouts-monitor-cleanup)
16. [State Machines](#16-state-machines)
17. [Namespace and Privilege Anatomy](#17-namespace-and-privilege-anatomy)
18. [Environment Reconstruction in Detail](#18-environment-reconstruction-in-detail)
19. [Identity, HOME, and Caches: Why HF Token Crashed](#19-identity-home-and-caches-why-hf-token-crashed)
20. [SSH Channel Types: Shell, Exec, Subsystem, Direct-TCPIP](#20-ssh-channel-types-shell-exec-subsystem-direct-tcpip)
21. [Heartbeat Command Delivery (No Inbound)](#21-heartbeat-command-delivery-no-inbound)
22. [Configuration Reference](#22-configuration-reference)
23. [File and Function Index](#23-file-and-function-index)
24. [Failure Matrix](#24-failure-matrix)
25. [Troubleshooting Recipes](#25-troubleshooting-recipes)
26. [Operator Runbook](#26-operator-runbook)
27. [Security Notes (Intentionally Relaxed) and Future Hardening](#27-security-notes-intentionally-relaxed-and-future-hardening)

---

## 1. Mental Model in 30 Seconds

Every interactive session is **two Docker containers on the same worker host**:

| Container | Name | Image | PID 1 | Role |
|---|---|---|---|---|
| **env** | `interactive-<sid[:24]>-env` | training image or `{user}/{job}-env:latest` | `sleep infinity` | Your VM. Holds GPUs, `/workspace` code, conda, pip packages. No sshd, no Tailscale. |
| **access** | `interactive-<sid[:24]>-access` | shared `aorko123/access-sshd:latest` | `sshd -D` + `tailscaled` | Jump host. Holds tailnet identity + sshd. No user code. Joins env PID **and** network namespaces. |

```
user laptop / VS Code
  |  ssh -p GATEWAY_SSH_PORT sandbox@gateway  (+ one-time password)
  v
+----------------+  verify-ephemeral  +------------------+
|  SSH Gateway   |-------------------->|   Scheduler      |
|  paramiko      |  returns            |  ephemeral passwd|
|  server        |  session_id +       |  store (TTL)     |
+----------------+  headscale_ip       +------------------+
  | ssh over tailnet (private key per session)
  v
+---------------------+  --pid container:<env>
|  access container   |  --network container:<env>
|  sshd + tailscaled  |--+
|  ForceCommand:      |  |
|   enter-env.sh      |  |
+---------------------+  |
                         v
                 +----------------------+
                 |     env container    |
                 |  sleep infinity (1)  |
                 |  /workspace          |
                 |  GPUs, conda, code   |
                 +----------------------+
```

Why split?

- Env stays a **clean portable training image**. It can be `docker commit`ed and run as a normal batch job later. No SSH keys, no tailnet keys baked in.
- All SSH/tailnet plumbing lives in **one shared access image** built once by the operator and reused by every session. Small per-job images, centralised security surface.
- Sharing PID + net namespaces + `nsenter` gives `docker exec`-like semantics without a Docker socket in the user path.

After this doc’s VM-hardening, the shell behaves like Ubuntu:

- `whoami` → `root` (via setuid nsenter), `sandbox(1000)` also exists with `NOPASSWD sudo`.
- `HOME` always writable, `USER/LOGNAME/SHELL/LANG/XDG_*` backfilled, ML caches writable.
- `ssh host "any command"`, `scp/sftp`, `ssh -L` port forwards, `sudo apt install`, `nvidia-smi`, Jupyter/TensorBoard ports reachable via tailnet.

---

## 2. Actors and Repositories

| Actor | Dir | Process | Interactive job |
|---|---|---|---|
| Scheduler | `Scheduler/app/` | FastAPI + Postgres + Redis | Creates session + job rows, mints SSH keypair (via Gateway) + Headscale pre-auth key, dispatches via `pull_job`, delivers `stop/commit` via heartbeat, issues one-time SSH passwords, mirrors `report_ip` state. |
| Docker Image Builder | `Docker_Image_Builder/` | `builder.py` poll loop | Polls `GET /jobs/unbuilt_jobs`. Derived (`base_job_id`): only `ensure_access_image()`. Direct (`object_key`): builds/pushes `{user}/{job}-env:latest` with VM defaults. Notifies `POST /jobs/mark_interactive_ready`. |
| Worker | `Worker/` | `main.py` heartbeat + job threads, `executor.py`, `interactive_handler.py` | Pulls `flag=interactive`, `docker pull` + `docker run` env+access, `_prepare_env_container`, polls tailnet IP, `POST /interactive/report_ip`, monitors timeouts, commit/push, stop. |
| Access image | `AccessContainer/` | `Dockerfile`, `access-entrypoint.sh`, `enter-env.sh` | Source of truth, built/pushed once by operator. `sshd + tailscaled + setuid nsenter + ForceCommand`. |
| Gateway | `gateway/` | `main.py` (FastAPI + tailscaled + SSH server), `ssh_server.py` (paramiko), `session_client.py`, `ssh_key_manager.py` | User-facing `ssh -p 2222 sandbox@gateway`. Verifies one-time password, proxies shell/exec/subsystem/direct-tcpip over tailnet with per-session private key. |
| Headscale mgmt | `headscale_mgmt/` | FastAPI wrapping `headscale` CLI | `POST /auth-keys` mints reusable+ephemeral pre-auth keys, `DELETE /auth-keys/{key}` revokes. Auth by `Bearer HEADSCALE_MGMT_AUTH_TOKEN`. |
| Object store | `Object_store/` | MinIO proxy | Only for direct builds (uploaded `.zip`) and build logs. Interactive runtime has **no volume mount** — `/workspace` is overlay, persisted only via `commit`. |
| Tailnet | Headscale server (external) | Control plane | All access containers + gateway join as ephemeral nodes (`100.x`). Users never join tailnet directly; gateway bridges them. |

Key IDs:

- `job.id` (uuid): interactive job row (`build_type=interactive`).
- `session.session_id` (uuid): also used as **tailnet hostname** (`tailscale up --hostname=$SESSION_ID`) and container name suffix.
- `session.gateway_session_id`: same uuid, key for gateway private key file `<SSH_KEY_DIR>/<session_id>`.
- Container names: `interactive-<session_id[:24]>-env` / `-access` (`interactive_handler._container_name`).

---

## 3. End-to-End Sequence

```
1. CREATE   User -> Scheduler POST /interactive/create {base_job_id}
              interactive_service.create_session()
                (a) job_service.create_interactive_job() -> Job(NOT_RUNNABLE, build_type=interactive)
                (b) Gateway POST /keys {session_id} -> ssh_public_key
                (c) headscale-mgmt POST /auth-keys -> headscale_auth_key (reusable, ephemeral, 3600s)
                (d) INSERT InteractiveSession(PENDING, worker_id=NULL)

2. BUILD    Builder scan_and_process() -> GET /jobs/unbuilt_jobs
              derived: ensure_access_image() only -> None
              direct : resolve_interactive_base_image() + download zip + build_push_and_clean(include_project=True)
              POST /jobs/mark_interactive_ready -> Job(INTERACTIVE_READY)

3. DISPATCH Worker job_loop -> POST /jobs/pull_job {worker_id, gpu_type, free_vram}
              _check_interactive_job_strategy(): oldest INTERACTIVE_READY + PENDING session
                (FOR UPDATE lock, skip last_worker_id==me, skip if worker busy)
              Job(INTERACTIVE_DEPLOYING), Session(DEPLOYING, worker_id=me)
              return {flag:interactive, env_image_tag, access_image_tag, headscale_url,
                      headscale_auth_key, ssh_public_key}

4. DEPLOY   Worker executor.process_job() -> handle_interactive()
              interactive_handler.run_interactive_session()
                docker pull env + access
                docker run env   (privileged, gpus, shm 8g, sleep infinity)
                docker run access (--pid + --network container:env, userns=host, cap ALL, /dev/net/tun)
                _prepare_env_container() (user+sudo+caches+tools)
                _wait_for_tailscale_ip() (docker logs "Tailscale IP: 100.x")
              POST /interactive/report_ip {RUNNING, ip}
              Scheduler: Session(RUNNING, headscale_ip=ip), Job(INTERACTIVE_RUNNING)
              + Redis SET interactive_heartbeat:<sid> (3600s)
              start _monitor_container thread

5. ACCESS   User -> Scheduler: get connect info (dashboard calls issue_ephemeral_password)
              password = token_urlsafe(12), store sha256 only, TTL 300s + 300s grace, cap 600s
            User: ssh -p GATEWAY_SSH_PORT sandbox@gateway-host, paste password
              gateway check_auth_password -> POST scheduler/verify-ephemeral -> {session_id, headscale_ip}
              gateway session_client.connect(session_id, headscale_ip) (private key, AutoAddPolicy)
              access sshd auth sandbox pubkey -> ForceCommand enter-env.sh
              enter-env.sh reconstructs env + nsenter -t 1 -m -u -i -n -p -> bash -l in /workspace

6. USE      shell/exec/sftp/direct-tcpip all proxied gateway->access->env (see §20)
            apt/pip/python/torch/HF all work as root or sudo sandbox

7. COMMIT   User asks commit -> job_service.commit_interactive_job(job_id, command, resume_command)
              Session(commit_pending=True, commit_image_tag=aorko123/<job>:latest)
              Job.command/resume_command stored now
            Worker heartbeat returns commit_sessions[] -> _run_commit thread
              docker commit --change 'CMD ["sh","-c",command]' env <tag> + docker push
              POST /jobs/{id}/commit_complete -> Job(VRAM_ESTIMATION_PENDING), clear pending
              (or commit_failed -> stay INTERACTIVE_RUNNING, retryable)

8. STOP     User stop / timeout / container exit / worker restart
              Scheduler Session(STOPPING) -> heartbeat stop_sessions[] -> worker _stop_container
                docker stop -t 10 env+access (fallback rm -f), remove_image(env tag)
              _monitor reports STOPPED/INTERACTIVE_STOPPED via report_ip
              Scheduler Session(STOPPED, headscale_ip=NULL), Job(INTERACTIVE_STOPPED)
              gateway key deleted, headscale key revoked (on user stop path)
```

---

## 4. Step 1 — Create: POST /interactive/create

File: `Scheduler/app/services/interactive_service.py:create_session()` + `app/api/interactive_route.py`.

Request (current schema only supports derived):

```json
POST /interactive/create
Authorization: Bearer <JWT>
{"base_job_id": "<training-job-uuid>"}
```

Validation:

- Needs `base_job_id` **or** `object_key`. Route today only exposes `base_job_id`; service supports both for direct uploads.
- Base job must exist and belong to `current_user.user_id`, else `400`.

Sub-steps:

**(a) Job row** — `job_service.create_interactive_job(db, {base_job_id, user_id, name, object_key, config})`:

- Derived dedupe: one interactive job per `(user_id, base_job_id)` via partial unique index `uq_jobs_base_job_interactive`. If existing in terminal (`INTERACTIVE_STOPPED/FAILED`), reset to `NOT_RUNNABLE` so retry doesn’t violate constraint; else return existing.
- New row: `id=uuid4(), build_type=interactive, status=NOT_RUNNABLE, command=""`, `base_job_id` set, `object_key` NULL for derived.

**(b) Gateway keypair** — `POST {GATEWAY_API_URL}/keys {session_id}` (`gateway/api.py:create_key` → `ssh_key_manager.generate_keypair`):

- ED25519 generated with `cryptography`, private PEM written `<SSH_KEY_DIR>/<session_id>` mode `0600`, public OpenSSH returned.
- Idempotent: if file exists, return existing pubkey.
- Scheduler stores pubkey on session row; **private never leaves gateway disk** (volume-mounted `/data/ssh-keys`).

**(c) Headscale pre-auth** — `POST {HEADSCALE_MGMT_URL}/auth-keys {expiry_seconds:3600, reusable:true, ephemeral:true}` with `Bearer HEADSCALE_MGMT_AUTH_TOKEN` (`headscale_mgmt/api.py` → `headscale_client.create_preauth_key` wrapping `headscale` CLI):

- Reusable: same key could join multiple nodes (only used once here).
- Ephemeral: node auto-removed from tailnet when offline (no stale `100.x`).
- On failure, gateway key deleted to avoid orphan.

**(d) Session row** — `InteractiveSession(id=uuid, job_id, user_id, base_job_id, session_id=uuid, gateway_session_id=same, ssh_public_key, headscale_auth_key, status=PENDING, worker_id=NULL)`.

Dedupe: if a `PENDING/DEPLOYING/RUNNING/STOPPING` session already exists for this `job_id`, return it instead of minting keys. Prevents double tailnet nodes on double-click/retry.

No worker contact happens here. Scheduling is pull-based.

---

## 5. Step 2 — Build: Builder Loop

Files: `Docker_Image_Builder/builder.py:scan_and_process()`, `docker_ops.py`, `api.py`, `database.py`.

Loop: `main()` → `init_db()` (sqlite `processed_jobs`, `base_images`) → every `POLL_INTERVAL`:
`docker_login()` → `prune_old_base_images(7d)` → `fetch_unbuilt_jobs()` (`GET /jobs/unbuilt_jobs` → `NOT_RUNNABLE` jobs).

Per job:

- **Validation**: training needs `object_key+docker_base_image`; interactive needs `base_job_id` **or** `object_key`.
- **Idempotency**: `is_job_processed(job_id)` (local sqlite). If already built but scheduler still lists unbuilt (notification lost), re-notify and skip build.
- **Derived** (`base_job_id` set): training image already exists (`aorko123/<base_job_id>:latest`). Only `ensure_access_image(client)`: `images.get(aorko123/access-sshd:latest)` else `pull`. Returns `None` on success (= ready), `("system", ...)` on pull fail (retried, not FAILED).
- **Direct** (no `base_job_id`): `resolve_interactive_base_image(env_config)`:
  1. `base_image` override verbatim,
  2. `pytorch_version` → `pytorch/pytorch:<ver>-cuda<cuda>-cudnn9-runtime` (or full variant if `-` in cuda),
  3. else `python:<ver>-slim` (default 3.11).
  Then `download_job_archive(object_key)` (object store `/objects/...`), `extract_job_archive` (tempdir), `find_project_dir` (first non-hidden subdir or root), `build_push_and_clean(..., build_type=interactive, include_project=True)`.

`generate_env_dockerfile(base, with_project, requirement_files)` (VM-hardened) bakes:

```dockerfile
FROM <base>
USER root
ENV PYTHONDONTWRITEBYTECODE=1 PIP_BREAK_SYSTEM_PACKAGES=1 DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8
RUN useradd sandbox(1000) + NOPASSWD sudoers
ENV XDG_CACHE_HOME=/tmp XDG_CONFIG_HOME=/tmp/.config XDG_DATA_HOME=/tmp/.local/share
ENV HF_HOME=/tmp/huggingface HF_HUB_CACHE=/tmp/huggingface/hub
ENV TRANSFORMERS_CACHE=/tmp/huggingface HF_DATASETS_CACHE=/tmp/huggingface/datasets
ENV MPLCONFIGDIR=/tmp/matplotlib TORCH_HOME=/tmp/torch PIP_CACHE_DIR=/tmp/pip
RUN mkdir -p <all caches> /workspace && chmod -R 777 /tmp/... && chmod 755 /root...
RUN apt-get install sudo git vim nano htop tmux openssh-client openssh-sftp-server curl ca-certificates tar gzip locales + locale-gen
WORKDIR /workspace (direct only) + COPY + pip install (chunked requirements-part-*.txt, 25 lines/chunk)
RUN root + sandbox .bash_profile sourcing .bashrc (unconditional, so bash -l gets conda)
CMD ["sleep", "infinity"]
```

Build: `client.images.build(path=build_dir, tag=<tag>)` with containerd-export retry (`DOCKER_BUILD_ATTEMPTS`, backoff), log streaming via `emit_build_lines` + `send_log_lines` + object-store `build.log` upload. Push single attempt (`images.push`). On success delete local copy, `ensure_access_image`.

- Tag: interactive → `{DOCKER_HUB_USERNAME}/{job_id}-env:latest` (note `-env` suffix; batch uses `{job_id}:latest`).
- Derived sessions never build env; they reuse base training image (which lacks VM bake — fixed at runtime by `_prepare_env_container`).

Notification:

- `result is None` → `notify_scheduler_interactive_ready(job_id)` → `POST /jobs/mark_interactive_ready` → `Job(INTERACTIVE_READY)` (only from `NOT_RUNNABLE/PENDING`). Then `mark_job_processed`.
- `("user", reason)` → `POST /jobs/mark_failed` → `FAILED` once (bad Dockerfile/requirements — conservative `_is_transient_build_error` avoids infinite retry).
- `("system", reason)` → keep pending, retried (network/registry/daemon).

---

## 6. Step 3 — Dispatch: pull_job Returns flag=interactive

Files: `Scheduler/app/services/job_service.py:_check_interactive_job_strategy`, `get_next_job_for_worker`, `app/api/worker_route.py` not involved (pull is `POST /jobs/pull_job`).

Worker `job_loop` (`Worker/main.py`) every `JOB_POLL_INTERVAL` (10s): `resume_persisted_job_if_any()` then `api.pull_job(gpu_type, free_vram)`.

Scheduler `get_next_job_for_worker`:

1. Worker must exist and not `is_testing`.
2. If worker already hosts `DEPLOYING/RUNNING/STOPPING` interactive session → return `None` (one interactive per worker; also blocks batch pulls while interactive active).
3. Strategies in order: interactive → vram_estimation → retry → training. First non-None wins.

`_check_interactive_job_strategy`:

- Skip if worker busy with batch (`IN_PROGRESS/VRAM_ESTIMATION_PENDING` via Redis `job_worker:<id>`).
- Oldest `Job(INTERACTIVE_READY)`, then its `PENDING` session (`FOR UPDATE` lock, `last_worker_id != me` to avoid flapping back to a host that just lost it).
- Transition: `Job(INTERACTIVE_DEPLOYING, started_at=now, device=gpu_type)`, `Session(DEPLOYING, worker_id=me, last_worker_id=me)`, Redis `SET job_worker:<job>=worker`.
- Payload (worker is scheduling-agnostic, everything needed is here):

```json
{
  "flag": "interactive",
  "id": "<job>", "job_id": "<job>", "session_id": "<uuid>",
  "env_image_tag": "aorko123/<base_job_id>:latest (derived) | aorko123/<job>-env:latest (direct)",
  "access_image_tag": "aorko123/access-sshd:latest",
  "headscale_url": "<HEADSCALE_URL>",
  "headscale_auth_key": "<pre-auth>",
  "ssh_public_key": "ssh-ed25519 AAAA... (gateway)",
  "status": "INTERACTIVE_DEPLOYING"
}
```

`Worker/executor.py:process_job` routes `flag==interactive` → `handle_interactive()` → `interactive_handler.run_interactive_session(...)` and returns (no batch pull).

---

## 7. Step 4 — Deploy: Worker Starts Two Containers

File: `Worker/interactive_handler.py:run_interactive_session()`.

Dedupe: if `session_id` already in `_active_containers`, return existing `access_id` (heartbeat redelivery safe).

**1. Pull** both tags (`docker pull`). On fail → `report_interactive_ip(FAILED)` and return.

Names: `interactive-<sid[:24]>-env`, `-access`. `_remove_container_if_exists` clears stale non-running same-name.

**2. Env** (`sleep infinity`, holds GPU + code):

```bash
docker run -d --rm \
  --gpus all \
  --privileged \
  --cap-add ALL \
  --security-opt seccomp=unconfined \
  --security-opt apparmor=unconfined \
  --shm-size=8g \
  --userns=host \
  --name interactive-<sid>-env \
  <env_image_tag> sleep infinity
```

- `--privileged` + `ALL` + unconfined seccomp/apparmor: VM-like (`apt`, `mount`, `iptables`, DiD shims). Security intentionally open.
- `--shm-size=8g`: PyTorch `DataLoader(num_workers>0)` needs large `/dev/shm` (default 64M OOMs).
- `--userns=host`: required even with `--privileged` when daemon has userns-remap; otherwise root maps to subordinate UID and setuid nsenter breaks.
- No `-v`, no `-p`, no `--network` flag → default bridge, overlay FS. No `USER` → image default (almost always root).

**3. Access** (jump host, shares PID **and** net):

```bash
docker run -d --rm \
  --pid container:interactive-<sid>-env \
  --network container:interactive-<sid>-env \
  --userns=host \
  --cap-add ALL \
  --device /dev/net/tun \
  -e HEADSCALE_URL=... -e HEADSCALE_AUTHKEY=... -e SESSION_ID=... -e SSH_PUBLIC_KEY=... \
  --name interactive-<sid>-access \
  aorko123/access-sshd:latest
```

- `--pid container:env`: access `/proc/1/environ` **is** env PID 1 env; `nsenter -t 1` enters env namespaces.
- `--network container:env`: single netns illusion. `tailscaled` (in access) creates `tailscale0` in env netns; user shell after `nsenter -n` stays in same netns, so `0.0.0.0:8888` in env is reachable via tailnet IP. Without this, Jupyter ports would be bridge-only and invisible on tailnet.
- `--cap-add ALL` + setuid nsenter: `sandbox` (1000) can `nsenter` (needs `CAP_SYS_ADMIN`). Entrypoint re-`chown root:root + chmod u+s /usr/bin/nsenter` at runtime to survive userns-remap layer remapping.
- `/dev/net/tun`: required by `tailscaled`.
- Env vars validated with `: "${VAR:?...}"` in entrypoint; missing → container exits → fail-fast IP poll.

Track `{env_id, access_id, env_name, access_name, env_image_tag}` in `_active_containers`, then `_prepare_env_container`, then IP poll, then report, then monitor thread.

---

## 8. Step 5 — Prepare: _prepare_env_container Makes It VM-like

File: `Worker/interactive_handler.py:_prepare_env_container(env_name, access_name)`. All `docker exec -u 0 <env> bash -c ...`, best-effort, never raises. Fixes **derived images** (old training images without VM bake) at runtime.

1. **Resolve UID/GID**: `docker exec <access> id -u/-g sandbox` (default `1000:1000`). Used for chown/useradd so both containers agree.

2. **Sandbox user + sudo** (VM owner semantics):
   ```bash
   id sandbox || (useradd -u <uid> sandbox || useradd sandbox)
   mkdir -p /home/sandbox && chown <uid>:<gid> && chmod 755
   echo 'sandbox ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/sandbox && chmod 440
   ```
   Default shell stays root via nsenter, but `su sandbox`, `sudo -u sandbox`, and tools refusing root now work.

3. **Project dir**: `mkdir -p /workspace || /output`. `enter-env.sh` does `cd /workspace || cd /`.

4. **Login profiles**: non-destructive `/root/.bash_profile` sourcing `~/.bashrc` (conda/virtualenv/PATH from base image), mirrors baked image. Also `/home/sandbox` profile.

5. **Generic HOME/XDG repair** (the HF-token class, fixed once for all tools):
   ```bash
   mkdir -p /root/.cache/huggingface /root/.config \
     /home/sandbox/.cache/huggingface ... /tmp/huggingface/hub ... /tmp/pip
   chmod 755 /root /root/.cache ...
   [ -f /root/.cache/huggingface/token ] && chmod 644 ...  # world-readable so uid 1000 doesn't crash
   chown -R <uid>:<gid> /home/sandbox/.cache/.config/.local
   chmod 1777 /tmp; chmod -R 777 /tmp/huggingface /tmp/matplotlib ...
   mkdir -p /workspace && chmod 777 /workspace
   ```
   See §19 for why `/root:700 + token:600 + uid 1000` killed `load_dataset`.

6. **VM tools** (Debian only): if `sudo/git/sftp-server` missing:
   ```bash
   apt-get update -qq && apt-get install -y sudo git vim nano htop tmux \
     openssh-client openssh-sftp-server ca-certificates curl tar gzip locales
   locale-gen C.UTF-8
   ```
   `sftp-server` + `openssh-client` (`scp`) are required by `enter-env.sh` exec path (§12); `curl/tar/gzip` by VS Code server installer. 600s timeout; non-Debian (alpine) skipped silently.

7. **Diagnostics**: `df -h /`, `ls -ld /home /home/sandbox` logged.

---

## 9. Step 6 — Tailnet Join and IP Detection

Access entrypoint (`AccessContainer/access-entrypoint.sh`):

1. Re-assert setuid nsenter.
2. `tailscaled --state=/var/lib/tailscale/tailscaled.state --socket=/var/run/tailscale/tailscaled.sock &`, wait for socket.
3. `tailscale up --login-server=$HEADSCALE_URL --authkey=$HEADSCALE_AUTHKEY --hostname=$SESSION_ID` (ephemeral+reusable key from §4; hostname is session uuid).
4. Install gateway pubkey: `/home/sandbox/.ssh/authorized_keys` (`700/600`, `sandbox:sandbox`).
5. Write `/etc/ssh/sshd_config.d/sandbox.conf`:
   ```
   PasswordAuthentication no
   PubkeyAuthentication yes
   PermitRootLogin no
   AllowUsers sandbox
   ForceCommand /usr/local/bin/enter-env.sh
   Subsystem sftp /usr/lib/openssh/sftp-server
   AllowTcpForwarding yes
   X11Forwarding no
   PermitTunnel no
   ```
6. `echo "Tailscale IP: $(tailscale ip -4)"` — **worker scrapes this**.
7. `exec /usr/sbin/sshd -D -e`.

Worker `_wait_for_tailscale_ip(session_id)` (`IP_LOG_RE = Tailscale IP:\s*(100\.\d+\.\d+\.\d+)`, `IP_POLL_INTERVAL=1s`, `INTERACTIVE_IP_TIMEOUT=30s`):

- Loop `docker logs <access>` (stdout+stderr) for regex.
- Fail-fast if `docker inspect {{.State.Running}}` != `true` (bad env vars, tailscale auth fail, sshd crash).
- Timeout → `_stop_container` + `report_ip(FAILED)`.

Gateway also joins tailnet at boot (`gateway/main.py:start_tailscale()` with its own long-lived pre-auth key, `--accept-dns=false` to keep Docker DNS `127.0.0.11`).

---

## 10. Step 7 — Connect Info and Ephemeral Password

Worker success → `api.report_interactive_ip(session_id, ip, "RUNNING")` (`Worker/api.py`, backoff `1,2,4`s) → `POST /interactive/report_ip` → `interactive_service.update_session_ip()`:

- `Session(RUNNING, headscale_ip=ip)`, `Job(INTERACTIVE_RUNNING)` (only if job not already in batch statuses — commit race guard).
- Redis `SET interactive_heartbeat:<sid> <now> EX 3600` (watchdog liveness).

Dashboard then requests connect info → `ephemeral_password_service.issue_ephemeral_password(username, session_id, headscale_ip)`:

- `password = secrets.token_urlsafe(12)`, store only `sha256` digest: `{username, session_id, headscale_ip, issued_at, expires_at=now+300, first_used_at=None}`.
- One valid password per session (reissue revokes old); lazy purge expired.
- User sees e.g. `ssh -p 2222 sandbox@gateway-host` + password (their UNIX user, typically `sandbox` or account name — must match `username` at verify).

Verify (`POST /interactive/sessions/verify-ephemeral {username, password}`, no JWT — gateway calls it):

- Digest lookup, `expires_at` + `username` check (mismatch deletes entry).
- First success: `first_used_at=now`, `expires_at=min(expires+300, issued+600)` — grace so VS Code’s multiple connections (exec probes + shell + sftp, no multiplexing) all succeed. Multi-use within window, hard cap 600s from issuance.

---

## 11. Step 8 — Gateway SSH Auth and Proxy

Files: `gateway/ssh_server.py` (paramiko `ServerInterface`), `session_client.py`, `ssh_key_manager.py`, `config.py`, `main.py`.

**Listen**: `0.0.0.0:GATEWAY_SSH_PORT` (2222), host key persisted `<SSH_KEY_DIR>/gateway_host_key` (ED25519, generated once, `0600`).

**Per TCP connection** (`_handle_connection`): `paramiko.Transport`, `start_server(GatewayServer())`, then accept loop (30s) — VS Code multiplexes many channels over one transport, each in own thread (`_handle_channel`).

**Auth** (`check_auth_password`): `POST {SCHEDULER_API_URL}/interactive/sessions/verify-ephemeral`. On 200 store `session_id/headscale_ip` on server object, return `AUTH_SUCCESSFUL`. `get_allowed_auths` advertises `password,publickey` (VS Code needs password listed); `check_auth_publickey` always fails (passwords only).

**Channel demux** (`_handle_channel`):

- `chan.gateway_event = Event()` per channel; request callbacks (`shell/exec/subsystem/direct-tcpip`) set `chan.gateway_request=(kind, payload)` + `event.set()`.
- Session channels: wait 1s for request; if none, check `_pending_direct` FIFO (direct-tcpip never sends shell/exec); else wait 14s more. No request → close.
- `shell` → `_proxy_shell`, `exec` → `_proxy_exec`, `subsystem` → `_proxy_subsystem`, `direct-tcpip` → `_proxy_direct_tcpip`.

**Proxy core** (`_proxy_loop(server_chan, client_chan)`): non-blocking `select` both directions 4KB, PTY resize propagation (`gateway_window_change` → `resize_pty`), EOF/close break. Byte-level — works for interactive, piped install scripts, sftp binary, forwarded TCP.

- `_proxy_shell`: `session_client.connect(session_id, headscale_ip)` (private key `<SSH_KEY_DIR>/<sid>`, `AutoAddPolicy` — container host keys ephemeral, blind trust for MVP, `SSH_CONNECT_TIMEOUT=10`). If client asked PTY (`gateway_pty`), `get_pty(term,w,h)` + `invoke_shell()`; else `invoke_shell()` without PTY so piped input isn’t echoed (VS Code install). Then proxy; propagate exit status (10s deadline).
- `_proxy_exec(command)`: `open_session()` + `exec_command(command)` on container, proxy, propagate status. Used by VS Code OS detection, `ssh host "cmd"`, `scp -t` (scp is exec under the hood).
- `_proxy_subsystem(name)`: `invoke_subsystem(name)` (sftp). Requires access `Subsystem sftp` + env `sftp-server` binary (§8, §12).
- `_proxy_direct_tcpip(origin, destination)`: `open_channel("direct-tcpip", (dest_host,dest_port), origin)` on container transport, proxy. `ssh -L 8888:localhost:8888` → client opens direct-tcpip `localhost:8888` to gateway; gateway dials same from access (shared netns → env’s Jupyter). `check_channel_direct_tcpip_request` appends FIFO and returns `OPEN_SUCCEEDED`; `check_port_forward_request` allows `-R`; `check_channel_env_request` allows `SendEnv LANG/...`.

`session_client.execute_command` (for `POST /connect` API) + `close` helpers round out the module.

---

## 12. Step 9 — Access Container: sshd + enter-env.sh + nsenter

This is the trick. Read `AccessContainer/enter-env.sh` fully — every line matters.

**Why nsenter**: access was started `--pid container:env`, so its `/proc/1` **is** env PID 1 (`sleep infinity`). `nsenter -t 1 -m -u -i -n -p` enters env mount, UTS, IPC, net, PID namespaces. Result ≈ `docker exec -it <env> bash` but reachable over tailnet ssh as non-root via setuid.

**Outer (access netns, user sandbox)**:

1. Read env environ: `cat /proc/1/environ` (world-readable → direct) else `nsenter -t 1 -p -- cat ...` (setuid escalation for root-owned). NUL-delimited.
2. Parse into `env1_lines`, dropping `PWD/OLDPWD/SHLVL/_` (transient/hostile) and `SSH_*` (access sshd vars, not env’s). `SSH_ORIGINAL_COMMAND` deliberately excluded here — forwarded separately (below) so client commands aren’t confused with env image vars.
3. Backfill `HOME=/root`, `PATH=conda-aware default`, `TERM=xterm` if missing.
4. Capture `SSH_ORIGINAL_COMMAND` from **this sshd session** (set by sshd when ForceCommand overrides exec/scp/subsystem):
   - `ssh host "python train.py"` → `"python train.py"`
   - `scp -t /workspace/x` → `"scp -t /workspace/x"`
   - sftp subsystem → contains `sftp-server`/`internal-sftp`.
5. `shell_quote()` every value (char-by-char single-quote) and build `exports=(export PATH=...; export HOME=...; export TERM=...; export <env vars>...; [export SSH_ORIGINAL_COMMAND=...])`, capped `MAX_BYTES=64000` to avoid `E2BIG`.

**Inner (env namespaces, after nsenter, usually root)** — assembled as one `sh -c` string:

```sh
export PATH=...; export HOME=...; export TERM=...; export <image ENV>...;
MYUID=$(id -u);
if [ "$MYUID" != "0" ] && [ "$HOME" = "/root" ]; then export HOME=/home/sandbox; fi;
mkdir -p "$HOME" || { export HOME=/tmp; mkdir -p "$HOME"; };
MYUSER=$(id -un || echo root);
export USER="${USER:-$MYUSER}"; export LOGNAME="${LOGNAME:-$USER}"; export SHELL="${SHELL:-/bin/bash}";
export LANG="${LANG:-C.UTF-8}"; export LC_ALL="${LC_ALL:-C.UTF-8}";
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}";
export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}";
export XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}";
export HF_HOME="${HF_HOME:-$XDG_CACHE_HOME/huggingface}";
export HF_HUB_CACHE="${HF_HUB_CACHE:-$HF_HOME/hub}";
export TRANSFORMERS_CACHE="${TRANSFORMERS_CACHE:-$HF_HOME}";
export HF_DATASETS_CACHE="${HF_DATASETS_CACHE:-$HF_HOME/datasets}";
export MPLCONFIGDIR="${MPLCONFIGDIR:-$XDG_CACHE_HOME/matplotlib}";
export TORCH_HOME="${TORCH_HOME:-$XDG_CACHE_HOME/torch}";
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-$XDG_CACHE_HOME/pip}";
export NPM_CONFIG_CACHE="${NPM_CONFIG_CACHE:-$XDG_CACHE_HOME/npm}";
mkdir -p "$XDG_CACHE_HOME/huggingface" "$XDG_CONFIG_HOME" ...;
cd /workspace || cd /;
if [ -n "${SSH_ORIGINAL_COMMAND:-}" ]; then
  case "$SSH_ORIGINAL_COMMAND" in
    *sftp-server*|*internal-sftp*)
      exec /usr/lib/openssh/sftp-server || /usr/lib/ssh/sftp-server || sftp-server || sh -c "$SSH_ORIGINAL_COMMAND";;
    *) exec /bin/sh -c "$SSH_ORIGINAL_COMMAND";;
  esac;
fi;
if command -v bash; then exec bash -l; elif command -v zsh; then exec zsh -l; else exec sh; fi
```

Then `exec nsenter -t 1 -m -u -i -n -p -- /bin/sh -c "$script"`.

Notes:

- Env passed **explicitly**, not `--preserve-environment` (which would keep access env — wrong conda/PATH).
- `cd` runs **after** nsenter (env mount ns where `/workspace` exists).
- `bash -l` sources baked `.bash_profile → .bashrc` (conda activate).
- sftp needs binary **in env** (`/usr/lib/openssh/sftp-server` via §8 bake/runtime install) because after `-m` only env mounts visible.
- If setuid lost and shell stays `1000` with `HOME=/root`, fallback to `/home/sandbox` prevents the whole `PermissionError` class (§19).

---

## 13. Step 10 — Using the Session Like a VM

Once landed (`root@/workspace`, conda PATH, `LANG=C.UTF-8`):

- **Python/ML**: `python3 train.py`, `pip install`, `load_dataset("imdb")`, `torch.cuda.is_available()`, `nvidia-smi` (GPUs via `--gpus all`), DataLoader workers (via `--shm-size=8g`).
- **System**: `sudo apt update && sudo apt install ffmpeg htop`, `su sandbox`, `sudo -u sandbox pip install`, `systemd`-less (PID 1 is `sleep`; use `nohup/tmux`, not `systemctl`).
- **Files**: `scp -P 2222 file sandbox@gateway:/workspace/`, SFTP via VS Code file sync, `/workspace` world-writable.
- **Ports**: `python -m http.server 8000`, `jupyter lab --ip=0.0.0.0 --port=8888`, `tensorboard --logdir runs --port=6006` all listen in shared netns → reachable via tailnet IP from gateway-forwarded `ssh -L 8888:localhost:8888 -p 2222 sandbox@gateway` or VS Code port forwarding (`direct-tcpip`).
- **VS Code Remote-SSH**: host `gateway`, port `2222`, user `sandbox`, password one-time. Extension opens exec (OS check) + shell (install `curl/tar/gzip` present) + sftp + port forwards — all proxied. Server installs to `HOME/.vscode-server` (writable via §12 fallback).

Ephemeral: everything in overlay. Reconnect within session resumes same container/files/processes (unless idle/timeout killed terminal — `_kill_terminal` does `pkill -TERM "[s]shd: sandbox"` in access + `"[b]ash -l"` in env). Survives only via **commit** (§14).

---

## 14. Step 11 — Commit: Turning /workspace Into a Batch Image

Goal: env overlay → pushable training image whose `CMD` runs training (not `sleep infinity`).

Scheduler: `job_service.commit_interactive_job(job_id, command, resume_command, priority, ...)` validates `Job(INTERACTIVE_RUNNING)` + `Session(RUNNING)` + no `commit_pending`, stores `command/resume_command/priority` now (survives slow push), sets `Session(commit_pending=True, commit_image_tag=aorko123/<job>:latest)`.

Delivery: `get_commit_sessions_for_worker(worker_id)` (`commit_pending=True`) → heartbeat `commit_sessions: [{session_id, job_id, image_tag, command}]` → worker `_handle_scheduler_commands` spawns `_run_commit` thread (push takes minutes, must not block heartbeat; `_in_flight_commits` dedupes redelivery).

`interactive_handler.commit_and_push_container(session_id, image_tag, command)`:

```bash
docker commit --change 'CMD ["sh","-c","<command>"]' interactive-<sid>-env <image_tag>
docker push <image_tag>
docker rmi <image_tag>  # free worker disk; image lives on Hub
```

- `--change CMD` is critical: without it batch would `sleep infinity` forever.
- `remove_image` protects base images (`pytorch`, `python`, `access-sshd`) and only deletes derived tags.

Report: `commit_complete(job_id)` → `POST /jobs/{id}/commit_complete` → `complete_commit()`: `Session(commit_pending=False)`, `Job(VRAM_ESTIMATION_PENDING)` (enters batch pipeline: VRAM estimate → `RUNNABLE` → training). Idempotent (second call no-op). `commit_failed(reason)` → clear pending, stay `INTERACTIVE_RUNNING` (retryable), store `failure_reason`.

---

## 15. Step 12 — Stop, Timeouts, Monitor, Cleanup

**User stop**: `POST /interactive/{sid}/stop` (owner only) → `interactive_service.stop_session()`:

- Delete gateway key (`DELETE /keys/{sid}`), revoke headscale key (`DELETE /auth-keys/{key}`) best-effort.
- `PENDING` (unclaimed): `STOPPED` outright + `Job(INTERACTIVE_STOPPED)`.
- Else: `STOPPING` (worker picks up via heartbeat; redelivered until `report_ip(STOPPED)`).

**Worker stop** (`_handle_scheduler_commands` → `stop_session_container(sid)` → `_stop_container(sid)`):

```bash
docker stop -t 10 interactive-<sid>-env; docker stop -t 10 interactive-<sid>-access
# fallback on fail (paused/OOM/dead): docker rm -f
# pop _active_containers; remove_image(env_image_tag)
```

If session untracked (worker restarted), report `STOPPED` so scheduler stops redelivering.

**Monitor** (`_monitor_container`, 5s loop, daemon thread per session):

| Condition | Const (default) | Report |
|---|---|---|
| env or access not `running` | — | `STOPPED` |
| hard cap since start | `INTERACTIVE_SESSION_TIMEOUT=3600` | `INTERACTIVE_STOPPED` (kill terminal first) |
| never connected | `INTERACTIVE_NO_CONNECT_TIMEOUT=600` | `INTERACTIVE_STOPPED` |
| idle after first connect | `INTERACTIVE_IDLE_TIMEOUT=1800` (`_has_active_connection` counts `sshd:` procs) | `INTERACTIVE_STOPPED` |
| `0` disables that cap | | |

Timeout path: `_kill_terminal(sid)` (`pkill -TERM "[s]shd: sandbox"` in access + `"[b]ash -l"` in env, bracket avoids self-match) → `_stop_container` → `report_ip`.

**Liveness**: worker heartbeat includes `interactive_ssessions[]`; scheduler `worker_service._update_interactive_heartbeats` refreshes Redis; watchdog finalises dead containers even if worker alive. `container_health.py` explicitly **skips** `interactive-*` (own monitor owns them; it only reaps stale `train-*`).

---

## 16. State Machines

**Session** (`InteractiveSessionStatus`):

```
PENDING --pull--> DEPLOYING --report RUNNING--> RUNNING
  |                   |                            |
  | stop while        | deploy fail                | stop/timeout/exit
  | unclaimed         | (pull/start/no IP)         |
  v                   v                            v
STOPPED <--STOPPING<------------------------------+
               ^                                  |
               | user stop / timeout              | commit doesn't change session
               +----------------------------------+ (stays RUNNING, commit_pending toggles)
FAILED reached via report_ip(FAILED)
```

**Job** (interactive slice):

```
NOT_RUNNABLE -> PENDING (builder claim) -> INTERACTIVE_READY (builder done)
  -> INTERACTIVE_DEPLOYING (pull) -> INTERACTIVE_RUNNING (report RUNNING)
  -> VRAM_ESTIMATION_PENDING (commit_complete) -> batch pipeline
  -> INTERACTIVE_STOPPED / FAILED (stop/fail)
```

Redis: `interactive_heartbeat:<sid>` (container liveness, 3600s), `job_worker:<job>` (stall watchdog mapping).

---

## 17. Namespace and Privilege Anatomy

`docker run` recap (current VM-relaxed):

- Env: `--privileged --cap-add ALL --security-opt seccomp=unconfined,apparmor=unconfined --shm-size=8g --userns=host --gpus all`.
- Access: `--pid container:env --network container:env --userns=host --cap-add ALL --device /dev/net/tun`.

| Namespace | Flag | Effect |
|---|---|---|
| PID | `--pid container:env` | `/proc/1` shared; `nsenter -t 1 -p` enters env pids; `ps` in shell shows env processes. |
| Net | `--network container:env` | Single stack; `tailscale0` visible to env; user ports on tailnet. |
| Mount | `nsenter -m` at shell time | Shell sees env `/workspace`, conda, GPUs `/dev` (nvidia runtime injects into env mounts). Host mounts hidden. |
| UTS/IPC | `nsenter -u -i` | Hostname/IPC isolated per env (tailscale hostname change doesn’t rename env). |
| User | `--userns=host` | Container root == host root (no remap). Required for setuid nsenter to work when daemon has userns-remap; `--privileged` alone doesn’t disable remap. |
| Setuid | `chmod u+s /usr/bin/nsenter` (baked + re-asserted) | `sandbox(1000)` gets euid 0 → `CAP_SYS_ADMIN` → nsenter succeeds. If lost, shell stays 1000 → HOME fallback (§19) saves you. |

`--privileged` ≈ `ALL caps + all devices + no seccomp/apparmor`. Needed for `mount/iptables`, DiD shims, `tailscaled` TUN. Trade-off accepted (see §27).

---

## 18. Environment Reconstruction in Detail

Problem: access env (Debian `PATH=/usr/local/sbin:...`, `HOME=/home/sandbox`) is **wrong** for training (need conda `/opt/conda/bin`, `HOME=/root`, image `ENV`). `nsenter --preserve-environment` would preserve wrong env. So `enter-env.sh` passes explicitly.

- Source: `/proc/1/environ` (env PID 1 = `docker run ... sleep infinity` → Docker injects image `ENV` + `PATH/HOME` from Dockerfile/base). NUL-split, drop `PWD/OLDPWD/SHLVL/_/SSH_*`, cap 64K.
- Backfill: `HOME=/root`, conda `PATH`, `TERM=xterm` if missing.
- Forward: `SSH_ORIGINAL_COMMAND` from sshd session (not from `/proc/1`).
- Inner: `export ...;` string via `sh -c`, then `cd /workspace`, then exec command or login shell (`bash -l → zsh -l → sh`). `bash -l` sources baked `.bash_profile → .bashrc` (conda init).

Limits: 64K cap drops rare huge envs (long `LD_LIBRARY_PATH` chains); `PWD` intentionally reset to `/workspace`.

---

## 19. Identity, HOME, and Caches: Why HF Token Crashed

Original crash:

```
load_dataset("imdb") → huggingface_hub get_token() → Path(/root/.cache/huggingface/token).read_text()
→ PermissionError: [Errno 13] /root/.cache/huggingface/token
```

Chain:

1. Image `HOME=/root`, `/root` mode `700`, `token` mode `600 root:root` (created by earlier root run or base image).
2. Shell `HOME=/root` (correct reconstruction) but `uid=1000` (setuid lost / userns-remap / `USER` override).
3. `open(/root/.../token)` needs `x` on `/root` + `r` on file → `EACCES`.
4. `huggingface_hub` doesn’t catch `PermissionError` → even public datasets die (token unneeded but read attempted).

Same class hits `pip`, `npm`, `matplotlib`, `torch.hub`, `.vscode-server` — all assume writable `HOME`.

Generic fix (all three layers, not just HF):

- **Bake**: `ENV XDG_*/HF_*/MPL/TORCH/PIP → /tmp/...` world-writable (`777`), `chmod 755 /root`, `sandbox` owned caches.
- **Runtime** (`_prepare_env_container`): `chmod 755 /root`, `chmod 644 token` (single-tenant container, readability > secrecy), `mkdir -p` all fallbacks, `1777 /tmp`, `777 /workspace`.
- **Login** (`enter-env.sh` inner): if `uid!=0 && HOME==/root` → `HOME=/home/sandbox` → `/tmp`; backfill `USER/LOGNAME/SHELL/LANG/XDG_*` + all ML caches from `HOME`; `mkdir -p` them. So **whatever UID**, `HOME` writable and caches resolve under it.

Debug in session: `id; echo $HOME; ls -ld /root /root/.cache/huggingface; ls -l .../token; env | grep -E 'HOME|USER|XDG|HF_|MPL|TORCH'`.

---

## 20. SSH Channel Types: Shell, Exec, Subsystem, Direct-TCPIP

Gateway (`ssh_server.py`) + access `sshd_config` + `enter-env.sh` must agree:

| Client op | SSH request | Gateway | Access sshd | Enter-env |
|---|---|---|---|---|
| Interactive terminal | `session` + `pty` + `shell` | `_proxy_shell` (PTY iff client asked) | `ForceCommand` | no `SSH_ORIGINAL_COMMAND` → `bash -l` |
| `ssh host "cmd"`, VS Code probe, `scp -t` | `session` + `exec "cmd"` | `_proxy_exec` (`exec_command`) | `ForceCommand` (ignores, runs script) | `sh -c "$SSH_ORIGINAL_COMMAND"`, propagate exit status |
| SFTP / VS Code sync | `session` + `subsystem sftp` | `_proxy_subsystem` (`invoke_subsystem`) | `Subsystem sftp` + `ForceCommand` | `*sftp-server*` → `exec sftp-server` in env mnt ns |
| `ssh -L 8888:localhost:8888` | `direct-tcpip localhost:8888` | `_proxy_direct_tcpip` (`open_channel direct-tcpip` to container) | `AllowTcpForwarding yes` | N/A (TCP, not shell; shared netns makes `localhost` == env) |
| `SendEnv LANG` | `env` | `check_channel_env_request → True` | `AcceptEnv` default | `LANG` backfilled if unset |

PTY nuance: VS Code opens shell **without** PTY for install script — gateway skips container PTY so piped input isn’t echoed back.

Exit statuses proxied (10s deadline) so `ssh host "pytest -q"; echo $?` works like VM.

---

## 21. Heartbeat Command Delivery (No Inbound)

Workers have no public IP / inbound port (behind NAT). Scheduler **never dials workers**. All control rides heartbeat:

- Worker `heartbeat_loop` every `HEARTBEAT_INTERVAL` (5s): `POST /workers/heartbeat {worker_id, gpu_type, free_vram, node_info, interactive_ssessions[]}`.
- Response `HeartbeatResponse {status, worker_id, stop_sessions[], commit_sessions[]}` from `get_stop_sessions(STOPPING)` + `get_commit_sessions(commit_pending)`.
- Worker `_handle_scheduler_commands`: stops (idempotent, `report STOPPED` if untracked) + commit threads (deduped `_in_flight_commits`).
- Redelivery until `report_ip` / `commit_complete/failed` confirms — safe because both ops idempotent.

---

## 22. Configuration Reference

**Worker** (`Worker/config.py`, env-overridable):

| Var | Default | Meaning |
|---|---|---|
| `SCHEDULER_URL` | required | Base for register/heartbeat/pull/report |
| `INTERACTIVE_IP_TIMEOUT` | 30 | `docker logs` tailnet IP wait |
| `INTERACTIVE_ACCESS_IMAGE` | `aorko123/access-sshd:latest` | Shared jump image |
| `INTERACTIVE_SESSION_TIMEOUT` | 3600 | Hard cap (0=off) |
| `INTERACTIVE_NO_CONNECT_TIMEOUT` | 600 | Never-connected cap |
| `INTERACTIVE_IDLE_TIMEOUT` | 1800 | Idle-after-connect cap |
| `DOCKER_HUB_USERNAME` | `aorko123` | Env tag prefix |
| `CONTAINER_AS_ROOT` | 0 | Batch only; interactive always root-capable |

**Builder** (`Docker_Image_Builder/config.py`): `SCHEDULER_QUEUE_URL`, `OBJECT_STORE_BUCKET=uploads`, `DOCKER_BUILD_ATTEMPTS=3`, `DOCKER_BUILD_CHUNK_SIZE=25`.

**Scheduler env**: `HEADSCALE_URL`, `PREAUTH_KEY_EXPIRY=3600`, `GATEWAY_API_URL=http://gateway:8200`, `HEADSCALE_MGMT_URL`, `DOCKER_HUB_USERNAME`.

**Gateway** (`gateway/config.py`): `GATEWAY_SSH_PORT=2222`, `SCHEDULER_API_URL`, `SSH_KEY_DIR=/data/ssh-keys`, `SSH_USER=sandbox`, `SSH_CONNECT_TIMEOUT=10`, `TAILSCALE_AUTH_KEY`, `HEADSCALE_URL`.

**Ephemeral passwords**: `ISSUE_TTL=300`, `GRACE=300`, `MAX_LIFETIME=600`.

---

## 23. File and Function Index

```
Scheduler.create_session (interactive_service.py:32)
  ├─ job_service.create_interactive_job (job_service.py:50)
  ├─ gateway POST /keys -> ssh_key_manager.generate_keypair
  ├─ headscale_mgmt POST /auth-keys
  └─ INSERT InteractiveSession(PENDING)

Builder.scan_and_process (builder.py:36)
  ├─ fetch_unbuilt_jobs
  ├─ derived: ensure_access_image (docker_ops.py)
  ├─ direct : resolve_interactive_base_image -> download/extract/find_project_dir
  │           -> build_push_and_clean(build_type=interactive)
  │              generate_env_dockerfile -> images.build -> images.push
  └─ notify_scheduler_interactive_ready -> mark_interactive_ready (INTERACTIVE_READY)

Scheduler pull (job_service.py:336 _check_interactive_job_strategy)
  └─ Session DEPLOYING, payload flag=interactive

Worker executor.process_job -> handle_interactive (executor.py)
  └─ interactive_handler.run_interactive_session (:37)
       ├─ docker pull env+access
       ├─ docker run env (privileged, gpus, shm)
       ├─ docker run access (--pid+--network container:env, tun)
       ├─ _prepare_env_container (user+sudo+caches+tools)
       ├─ _wait_for_tailscale_ip (logs regex)
       ├─ api.report_interactive_ip(RUNNING) -> Scheduler update_session_ip
       └─ _monitor_container thread (_has_active/_kill_terminal/_stop_container)
            commit_and_push_container (docker commit --change CMD + push)

User access
  ├─ issue_ephemeral_password (ephemeral_password_service.py:49)
  ├─ gateway check_auth_password -> verify_ephemeral_password_route
  ├─ session_client.connect (paramiko, private key, AutoAddPolicy)
  ├─ _proxy_shell/_proxy_exec/_proxy_subsystem/_proxy_direct_tcpip + _proxy_loop
  └─ access sshd -> enter-env.sh -> nsenter -t 1 -m -u -i -n -p -> bash -l /workspace

Stop/commit via heartbeat (Worker/main.py:41, Scheduler worker_route.py:28)
```

---

## 24. Failure Matrix

| Failure | Detected by | Result |
|---|---|---|
| Bad Dockerfile/requirements (direct) | `_is_transient_build_error=False` | `user` → `FAILED` once |
| Network/registry/daemon build | transient substrings | `system` → retried |
| Access image unpullable | `ensure_access_image` / `docker pull` | `report FAILED` |
| Env/access fail start | `docker run` rc | both stopped, `FAILED` |
| No `Tailscale IP` in 30s | `_wait_for_tailscale_ip` | stopped, `FAILED` |
| Access dies during poll | `inspect Running` | fail-fast `FAILED` |
| Idle/never-connected/over-cap | `_monitor_container` | kill terminal, `INTERACTIVE_STOPPED` |
| Worker restart mid-session | Redis heartbeat + watchdog | requeue/finalise, stop redelivery safe |
| Commit/push fail | `commit_and_push_container` | `commit_failed`, stay `RUNNING` retryable |
| HF token unreadable (old images) | runtime prep + enter-env fallback | self-healed to `/home/sandbox` or `/tmp` |
| sftp-server missing (old images) | runtime apt install | installed best-effort, else exec fallback |

---

## 25. Troubleshooting Recipes

- `PermissionError: /root/.cache/huggingface/token` (should be gone; if on old session): `export HF_HOME=/tmp/huggingface HF_HUB_CACHE=/tmp/huggingface/hub ...; mkdir -p $HF_HOME` or `rm -f /root/.cache/huggingface/token`. Diagnose: `id; echo $HOME; ls -ld /root; ls -l .../token`.
- `ssh host "cmd"` runs shell not cmd: old access image without `SSH_ORIGINAL_COMMAND` support — rebuild access image.
- `sftp: subsystem request failed`: old env without `openssh-sftp-server` — runtime installer should fix; check `ls /usr/lib/openssh/sftp-server` in env, `Subsystem sftp` in access.
- `ssh -L` hangs: old gateway without `direct-tcpip` or old access without shared netns — need both; check `AllowTcpForwarding yes` + shared `--network`.
- `DataLoader worker exited unexpectedly / shm`: needs `--shm-size=8g` (now default); check `df -h /dev/shm`.
- `No Tailscale IP`: `docker logs interactive-<sid>-access`, check `HEADSCALE_URL/AUTHKEY`, `tailscaled.sock`, pre-auth expiry (3600s).
- `apt lock / no sudo`: `which sudo; sudo -v`; runtime should have installed; if Alpine base, use `apk`.
- VS Code keeps asking password: one-time password expired (>600s) or reissued (old revoked) — request fresh connect info; first use grants grace for parallel channels.

---

## 26. Operator Runbook

```bash
# Rebuild shared access image (enter-env + sshd_config changes)
docker build -t aorko123/access-sshd:latest ./AccessContainer
docker push aorko123/access-sshd:latest

# Redeploy Builder (docker_ops.py env/access generators), Worker (interactive_handler.py), Gateway (ssh_server.py)
# No DB migration (no schema change in this VM pass).

# Per-session debug on worker host:
docker ps --filter name=interactive-<sid24>
docker logs interactive-<sid24>-access | grep -i -E 'tailscale|sshd|error'
docker exec -u 0 interactive-<sid24>-env id\; echo HOME=$HOME\; ls -ld /root /workspace
docker exec interactive-<sid24>-access cat /etc/ssh/sshd_config.d/sandbox.conf
tailscale status  # on worker if tailnet-wide issue
```

New direct env images get VM bake automatically; derived sessions (old training images) healed at runtime (user+sudo+caches+tools, up to ~10min apt on first start if missing).

---

## 27. Security Notes (Intentionally Relaxed) and Future Hardening

Current posture (per request — **do not use multi-tenant untrusted**):

- Env `--privileged + ALL caps + unconfined seccomp/apparmor`, access `--cap-add ALL --userns=host`, setuid nsenter, root shell, `NOPASSWD sudo`, `644` HF token, `777 /workspace /tmp/...`, gateway `AutoAddPolicy` (blind host-key trust), ephemeral tailnet keys reusable.

To harden later:

- Drop to minimal caps (`SYS_ADMIN+SYS_PTRACE+NET_ADMIN` for nsenter/tailscale only), re-enable seccomp/apparmor, remove `--privileged`, use `--pids-limit`, `--memory/--cpus`, disk quotas.
- Userns-remap properly (fix setuid ownership story instead of `--userns=host` bypass).
- Per-session persistent home volume (instead of `777`), `600` tokens with correct ownership (instead of `644`), short-lived non-reusable pre-auth keys, `StrictHostKeyChecking` with pinned host keys (instead of ephemeral + AutoAdd).
- NetworkPolicy: tailnet ACLs per session/user, egress allowlist for training (HF, PyPI, GitHub) instead of full internet.
- Audit: ship `enter-env` + `sshd` logs, preserve `bash_history` on commit scrubbed of secrets.

