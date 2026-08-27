# Implementation Instruction: Gateway SSH Bastion + Connect UI

> Resume point: the PLANNER stage is complete (final, non-blocked plan below).
> The CODER stage was rate-limited. Re-invoke the coder with this file's
> "FINAL PLAN" section verbatim when capacity returns.

Repo root: /Users/shahirbinzulfikeraorko/workspace/Distributed_ML_Training_scheduler

## Background / decisions already made

- The Scheduler already schedules interactive images on the worker and the
  container joins the headscale tailnet with hostname = `session_id` (a UUID)
  and reports its tailnet IP (`100.x.x.x`) into `interactive_sessions.headscale_ip`.
- The gateway currently has NO SSH server — only an HTTP API (port 8200) and
  uses paramiko client-side (`session_client.py`) to reach containers. So a
  bastion must be ADDED.
- User connects to the gateway bastion with their **platform username +
  password** (hashed password in `Scheduler/app/models/user_model.py`). The user
  holds NO SSH key. The gateway holds the per-session private key
  (`ssh_key_manager.py`) and proxies into the container. This is the bastion model.
- Container SSH user is `sandbox` (AccessContainer `AllowUsers sandbox`,
  `config.SSH_USER = "sandbox"`). The gateway always connects as `sandbox`;
  `container_user` is returned to the frontend for display only.
- A user has at most one active interactive session; gateway picks the newest
  if multiple.
- If `GATEWAY_PUBLIC_HOST` is empty, the frontend "not ready" fallback triggers.

## Files to read first (match existing patterns)

- Scheduler/app/api/jobs_route.py
- Scheduler/app/api/interactive_route.py
- Scheduler/app/services/interactive_service.py
- Scheduler/app/models/interactive_session_model.py (InteractiveSession fields:
  session_id, headscale_ip, user_id, job_id, status; and InteractiveSessionStatus enum)
- Scheduler/app/api/auth_route.py (confirm `POST /auth/login` request/response
  shape — likely {"username","password"} -> {"access_token","token_type"})
- Scheduler/app/db/database.py (how get_db / get_current_active_user are imported)
- gateway/config.py, gateway/main.py, gateway/session_client.py, gateway/ssh_key_manager.py
- Scheduler/docker-compose.yml
- UI/User/src/services/jobs.ts and UI/User/src/components/ConnectOptionsModal.tsx

---

# FINAL PLAN (pass this to the coder)

## A. Gateway bastion server
1. `gateway/Dockerfile`: change the apt install line to also install
   `openssh-server` (e.g.
   `RUN apt-get update && apt-get install -y --no-install-recommends openssh-client openssh-server ca-certificates curl \`).
2. `gateway/config.py`: add
   - `GATEWAY_SSH_PORT = int(os.getenv("GATEWAY_SSH_PORT", "2222"))`
   - `GATEWAY_PUBLIC_HOST = os.getenv("GATEWAY_PUBLIC_HOST", "")`
   - `GATEWAY_HOST_KEY_PATH = os.getenv("GATEWAY_HOST_KEY_PATH", "/data/ssh-keys/gateway_host_key")`
   - `SCHEDULER_API_URL` already exists; keep it.
3. `gateway/ssh_server.py` (NEW). Implement a paramiko server-mode SSH bastion:
   - On startup, load or generate an Ed25519 host key at
     `config.GATEWAY_HOST_KEY_PATH` (persist it; reuse serialization helpers
     from `ssh_key_manager` if available, otherwise use
     `cryptography.hazmat.primitives.asymmetric.ed25519.Ed25519PrivateKey` +
     `serialization`).
   - `class GatewayServer(paramiko.ServerInterface)`:
     - `check_auth_password(username, password)`: call Scheduler
       `POST {SCHEDULER_API_URL}/auth/login` with
       `{"username": username, "password": password}` using `requests`.
       On 200, capture `access_token`; then call
       `GET {SCHEDULER_API_URL}/interactive/sessions/active` with header
       `Authorization: Bearer <token>`. If that returns a session
       (`session_id`, `headscale_ip`), store them on the instance and return
       `paramiko.AUTH_SUCCESSFUL`. Otherwise (bad creds, or no active session)
       return `paramiko.AUTH_FAILED`. Wrap all calls in try/except so failures
       return `AUTH_FAILED`.
     - `check_channel_request(kind, chanid)`: return `paramiko.OPEN_SUCCEEDED`
       for `"session"`, else `paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED`.
     - `check_channel_shell_request(channel)`: return `True`, then spawn a
       thread that: (a) opens a paramiko client to the container via
       `session_client.connect(session_id, headscale_ip)` (uses the stored
       session key and connects as `config.SSH_USER` = `sandbox`), (b) proxies
       bytes bidirectionally between the server channel and the client channel,
       (c) on either side closing, closes both and cleans up.
   - Proxy loop: set both channels non-blocking (`setblocking(0)`), use
     `select.select` on the two channels, `recv`/`sendall` in both directions;
     forward `resize` (window-change) requests from the server channel to the
     client channel; on EOF/exception close both.
   - `start_ssh_server()`: bind `0.0.0.0:{GATEWAY_SSH_PORT}`, accept loop in a
     daemon thread; per connection create `paramiko.Transport(sock)`,
     `transport.add_server_key(host_key)`,
     `transport.start_server(server=GatewayServer())`; handle
     `transport.accept()` channels. Return the listening socket so it can be
     closed on shutdown.
   - `stop_ssh_server(sock)`: close the listening socket.
4. `gateway/main.py`: in the lifespan, after `start_tailscale()` (or
   equivalent), call `start_ssh_server()` to spawn the accept-loop thread (store
   the socket). In the shutdown block, call `stop_ssh_server(...)`.

## B. Scheduler endpoints
5. `Scheduler/app/services/interactive_service.py`: add
   ```python
   def get_active_session_for_user(db, user_id):
       return (db.query(InteractiveSession)
               .filter(InteractiveSession.user_id == user_id,
                       InteractiveSession.status == InteractiveSessionStatus.RUNNING)
               .order_by(InteractiveSession.created_at.desc())
               .first())
   ```
6. `Scheduler/app/api/interactive_route.py`: add `GET /interactive/sessions/active`
   protected by `get_current_active_user`. Return
   `{"session_id": ..., "headscale_ip": ...}` for the user's active session, or
   `HTTPException(404, "No active interactive session")` if none.
7. `Scheduler/app/api/jobs_route.py`: add `GET /jobs/{job_id}/connect`:
   - `current_user: User = Depends(get_current_active_user)`,
     `db: Session = Depends(get_db)`.
   - Load the job via the existing helper (e.g.
     `job_service.get_user_job_by_id(db, current_user.user_id, job_id)`); if
     `None` -> `HTTPException(404, "Job not found")`.
   - Find the running interactive session for that job:
     `db.query(InteractiveSession).filter(InteractiveSession.job_id == job_id, InteractiveSession.status == InteractiveSessionStatus.RUNNING).first()`.
     If none -> `HTTPException(404, "No running interactive session for this job")`.
   - Return:
     ```json
     {
       "session_id": session.session_id,
       "headscale_ip": session.headscale_ip,
       "gateway_host": os.getenv("GATEWAY_PUBLIC_HOST", ""),
       "gateway_ssh_port": int(os.getenv("GATEWAY_SSH_PORT", "2222")),
       "ssh_user": current_user.username,
       "container_user": "sandbox"
     }
     ```
   - Import `InteractiveSession`, `InteractiveSessionStatus`, and `os` as needed.

## C. Docker compose
8. `Scheduler/docker-compose.yml`:
   - `gateway` service: publish the SSH port. Keep `expose: ["8200"]` and add
     `ports: ["${GATEWAY_SSH_PORT:-2222}:2222"]`. Add env:
     `SCHEDULER_API_URL: ${SCHEDULER_API_URL:-http://api:8000}`,
     `GATEWAY_SSH_PORT: ${GATEWAY_SSH_PORT:-2222}`,
     `GATEWAY_PUBLIC_HOST: ${GATEWAY_PUBLIC_HOST:-}`.
   - `api` service: add env `GATEWAY_PUBLIC_HOST: ${GATEWAY_PUBLIC_HOST:-}` and
     `GATEWAY_SSH_PORT: ${GATEWAY_SSH_PORT:-2222}`.

## D. Frontend
9. `UI/User/src/services/jobs.ts`: add
   ```ts
   export interface JobConnectInfo {
     session_id: string;
     headscale_ip: string;
     gateway_host: string;
     gateway_ssh_port: number;
     ssh_user: string;
     container_user: string;
   }
   export const fetchJobConnectInfo = async (jobId: string): Promise<JobConnectInfo> => {
     const data = await api.get<JobConnectInfo | { error: string }>(`/jobs/${jobId}/connect`);
     if (hasError(data)) throw new Error(data.error);
     return data as JobConnectInfo;
   };
   ```
   (Match the existing `api` client and `hasError` helper usage in that file —
   inspect the file first.)
10. `UI/User/src/components/ConnectOptionsModal.tsx`:
    - Import `JobConnectInfo` and `fetchJobConnectInfo` from the jobs service.
    - Add state: `connectInfo: JobConnectInfo | null`, `connectError: boolean`,
      `loadingConnect: boolean`.
    - In the existing mount `useEffect`, call `fetchJobConnectInfo(job.id)`; set
      `connectInfo` on success, `connectError` on failure, and clear
      `loadingConnect`.
    - Replace the hardcoded `sshCommand` with:
      ```ts
      const sshCommand = connectInfo
        ? `ssh -p ${connectInfo.gateway_ssh_port} ${connectInfo.ssh_user}@${connectInfo.gateway_host}`
        : '';
      ```
    - In the SSH card: if `loadingConnect` show a spinner/"Loading…"; if
      `connectError` or no `connectInfo` (or `connectInfo.gateway_host` is
      empty), show a "Session not ready" fallback message in place of the
      command block and disable the copy button; otherwise show the command in
      the existing command block with the copy button (keep the existing copy
      handler).
    - Jupyter card: change the "Launch Notebook" button to a disabled button
      with text "Coming soon" (remove its onClick navigation and remove the
      `useNavigate` import if it becomes unused). Keep the card visible.

## Notes / assumptions to honor
- Container SSH user is `sandbox`. Gateway always connects as `sandbox`;
  `container_user` is display-only.
- A user has at most one active interactive session; gateway picks the newest.
- If `GATEWAY_PUBLIC_HOST` is empty, frontend "not ready" fallback triggers.
- Keep existing code/style; do not break other endpoints.

## Self-checks the coder must run before returning
- `cd Scheduler && python -c "import app.api.jobs_route, app.api.interactive_route, app.services.interactive_service"`
- `cd gateway && python -c "import ssh_server, main, config"`
- `cd Scheduler && docker compose config` (or `docker-compose config`); if
  docker compose unavailable, validate YAML with python.
- `cd UI/User && npx tsc --noEmit` if toolchain present; else visually confirm
  type consistency.

## Done criteria (for the verifier stage)
1. `GET /jobs/{job_id}/connect` (valid Bearer token) for a job with a RUNNING
   interactive session returns `session_id`, `headscale_ip`, `gateway_host`,
   `gateway_ssh_port`, `ssh_user` (= caller's username), `container_user`
   (`sandbox`). 404 for a job with no running session, and 404 for a job not
   owned by the caller.
2. Gateway auth: `POST {SCHEDULER_API_URL}/auth/login` is called on SSH login;
   wrong password -> `AUTH_FAILED`; correct password + active session ->
   `AUTH_SUCCESSFUL`.
3. Proxy: from a machine NOT on the tailnet,
   `ssh -p <gateway_ssh_port> <username>@<gateway_host>` (password = platform
   password) lands in the container shell. No SSH key needed by the user.
4. Frontend: `ConnectOptionsModal` shows the real copyable command
   `ssh -p <port> <username>@<gateway_host>` with a working copy button; a
   non-running job shows "Session not ready"; the Jupyter card shows a disabled
   "Coming soon" button and does not navigate.
5. Compose: `docker compose config` shows the gateway port published and the new
   env vars wired to both `api` and `gateway`; `docker compose up` starts the
   gateway with the SSH server listening on `GATEWAY_SSH_PORT`.
