# Interactive Editor `UNAVAILABLE` — Problem Summary for Agents

Date: 2026-09-20. Repo: `/home/ubuntu/Distributed_ML_Training_scheduler` (this machine = scheduler + gateway + headscale_mgmt; UI on another VM; worker on another machine).

## Symptom
`InteractiveCreate` → worker pulls image fine → Connect → `InteractiveEditor` shows `Disconnected`. Browser console on Refresh: `Uncaught (in promise) Error: UNAVAILABLE at receive → feed → socket.onmessage` (`UI/User/src/services/workspaceProtocol.ts:31`, global `Type.ERROR` record, or `RESULT.error` rejection — both surface as `UNAVAILABLE`).

## Established facts (do NOT re-investigate)
1. **Gateway is innocent.** `dml-interactive-gateway-1` logged both WSS sessions to resource `66527df7-.../workspace` with `outcome=1000` (normal close, bytes flowed both ways). Gateway never emits the string `UNAVAILABLE` (only numeric WS closes 4401/4403/4408/4410/1011/1013 in `Gateway/interactive_gateway/main.py`).
2. **Workload container is healthy.** `dml-66527df7-...-workload` (Ubuntu 22.04, PyTorch 2.6.0): `Running=true`, `User=10001:10001`, `Workdir=/workspace`, `/workspace` exists with project files, broker socket accepts, cgroup safety check passes, `python3` works as 10001. (An early "missing /workspace" report was a misread of `ls -la /`.)
3. **Direct-broker probe proved the faulty leg** (`sudo python3 /tmp/probe_broker.py` speaking the Unix-socket protocol in `Access_Container/interactive_access/protocol.py`):
   - `HELLO → 33 WORKSPACE_READY ✓`, `PTY_OPEN → 39 PTY_OPENED ✓`
   - `LIST("") → 35 {error: UNAVAILABLE} ✗` → **FileService is the sole failure point.**
4. **Root cause #1 (fixed in repo):** `Worker/interactive/file_service.py:184` passed unsupported `stdin=True` to docker-py 7.2.0 `exec_start(exec_id, detach, tty, stream, socket, demux)`. Every file op raised `TypeError`, swallowed by `except Exception → FileServiceError("UNAVAILABLE")` (line 216). Fix (committed as `c16a372`, deployed to all machines): drop `stdin=True` (`exec_create` line 179 already sets `stdin=True`). `Worker/interactive/broker.py:52` was already correct.
5. **Access-log interpretation guide** (`session.py:207`): `DISCONNECTED dur~0 input=0 output=0` = gateway probes (noise, ignore). Real sessions: `09:56:38 EXIT dur=0.255` (first user connect, died fast = our bug) and `09:56:45 BUSY` (retry hit `ACCESS_CAPACITY=1` while slot held — a *different* code, don't chase it as root cause).
6. **Latest state:** fix deployed everywhere + worker restarted, but a manual `FileService.call('list')` test then failed with `404 No such container: 53285506ccae` — that ID is the **pre-restart workload**, reaped by the restart. It tells us nothing about the fix. The "protocol error" now seen in the UI is most likely a stale-generation grant for the dead pre-restart runtime.

## Next steps (whoever picks this up)
1. Worker machine: `sudo docker ps | grep dml-` — if no workload, the restart cleaned the old assignment (expected). **Start a fresh runtime from the UI** (new assignment, fixed code from boot) and retest the editor.
2. If running: re-run the FileService check with the **current** container ID (not `53285506ccae`):
   `sudo /opt/dml/venv/bin/python -c` with `sys.path.insert(0, "/opt/dml/Worker")`, `FileService(docker.from_env(), "<current-id>", "10001", "/workspace").call("list", path="")` → expect `{'entries': [...]}`.
3. If fresh runtime still returns `UNAVAILABLE`: send new assignment ID + `sudo docker logs dml-<new>-access 2>&1 | grep -v DISCONNECTED | tail` + rerun `/tmp/probe_broker.py` (it takes the assignment ID from its `ASSIGN` var — update it).
4. Follow-ups (not blocking): add a `FileService` unit test asserting `exec_start` called without `stdin` (zero coverage today; `MagicMock` would have hidden this); consider logging the original exception in `FileService.call` instead of `raise ... from None`; `ACCESS_CAPACITY=1` + probes makes rapid retries randomly fail with `BUSY` — consider friendlier UX.

## Key files
- `Worker/interactive/file_service.py` (bug + fix), `Worker/interactive/broker.py:52`, `Worker/interactive/workspace_broker.py:159`, `Worker/interactive/docker_ops.py`, `Worker/interactive/manager.py`
- `Access_Container/interactive_access/session.py` (outcome codes), `.../protocol.py` (framing `!BBI`, Type values), `.../config.py` (capacity=1)
- `Gateway/interactive_gateway/main.py`, `.../relay.py`, `.../schemas.py`
- `UI/User/src/services/workspaceProtocol.ts`, `UI/User/src/pages/InteractiveEditor.tsx`
- Worker host debug scripts: `/tmp/probe_broker.py` (direct broker HELLO/LIST/PTY test), `/tmp/debug_fs.py` (raw exec probe). Must run with `sudo` + `/opt/dml/venv/bin/python`; paste via `tee ... <<'PYEOF'` heredoc, never paste Python straight into bash.
