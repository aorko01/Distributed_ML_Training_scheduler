# Access_Container — Duties and How It Implements Them

`Access_Container/` is a generic, Python-standard-library-only **authenticated terminal proxy endpoint**. It contains only `interactive_access/`, never the test PTY broker or user project files.

Its single duty: sit between the Tailnet side (Tailscale Serve → `127.0.0.1:9000`) and the runtime-bound Worker broker (unix socket), authenticate to that broker per session, strictly validate `terminal-stream-v1` / `workspace-stream-v1` / `SSH_OPEN` framing, and relay bytes. It never executes shells, never touches Docker/GPU, never interprets paths or Docker IDs.

See `Access_Container/README.md`, `docs/terminal-stream-v1.md`, `docs/interactive-access-contract.md`.

## 1. What it does (duties)

| Duty | Rule |
|---|---|
| Narrow proxy only | No local shell fallback, no `Connect`/`Save`, no scheduling/placement/snapshots. Production Docker exec/GPU/isolation are future Worker gates. |
| Gated listener | Terminal listener on loopback `9000` opens only after readiness succeeds, closes on failed periodic broker check. Health on loopback `9002`. No published ports. |
| Health reporting | `GET /health/live` → liveness. `GET /health/ready` → bounded authenticated broker probe (`PROBE`→`READY`), generic `{"status":"ok"/"unavailable"}` only. Controller must verify readiness before registration and withdraw Serve readiness when this fails — a TCP-only Serve probe cannot certify backend readiness. |
| Per-session broker auth | Every session (`OPEN`, `HELLO`, `SSH_OPEN`, readiness probe) does `CHALLENGE` → `AUTH` → `AUTHENTICATED` over the runtime unix socket with the mounted token. |
| Three relay modes | `terminal-stream-v1` PTY, `workspace-stream-v1` file+PTY relay, versioned `SSH_OPEN`→`SSH_READY`→raw relay for VS Code Remote-SSH. |
| Strict validation + isolation | Loopback-only bind, opaque `runtime_id`-scoped socket/token paths, token hygiene, capacity limits, bounded timeouts/sizes, no payload logging (IDs/outcomes/durations/byte counts only). |
| Hardened image | Pinned `python:3.11.14-slim-bookworm@sha256:…` image, `USER 10001:10001`, `HEALTHCHECK` on `/health/ready`, `CMD python -m interactive_access.main`. Launch read-only root, `/tmp` tmpfs, drop ALL caps, no-new-privs, PID/mem/CPU limits, sidecar netns, mount only runtime socket+token read-only, never Docker socket, pin by digest. |

## 2. How it does it (by file)

### `interactive_access/config.py` — safe startup
- `Config.from_env()`: reads `ACCESS_BROKER_SOCKET`, `ACCESS_BROKER_TOKEN_FILE`, `ACCESS_RUNTIME_ID`, optional `ACCESS_HOST` (default `127.0.0.1`), `ACCESS_PORT` (9000), `ACCESS_HEALTH_PORT` (9002), `ACCESS_CAPACITY` (1), `ACCESS_SSH_CAPACITY` (8). Example in `.env.example`.
- `validate()`: host must be loopback (`ipaddress`); `runtime_id` must match `[a-zA-Z0-9_-]{1,64}`; socket/token must be absolute, exactly under `/run/dml-interactive/<runtime_id>/`, no `..`, no symlinks in any parent; `capacity 1-16`, `ssh_capacity 1-32`, `port != health_port`; all deadlines `0 < v <= 60`.
- `read_secret(path)`: `O_NOFOLLOW|O_NONBLOCK`, must be regular file, no group/other write/exec (`& 0o027`), `≤256B`, `32-256B` printable ASCII, `≥8` distinct bytes, rejects `changeme`/`example`.
- `credentials()`: re-validates, asserts socket is `S_ISSOCK` (no-symlink stat), returns token.

### `interactive_access/broker_client.py` — broker auth
- `connect(config, clock)`: `config.credentials()` → `asyncio.open_unix_connection(socket)` → expect `CHALLENGE` + 32B → `proof = HMAC-SHA256(token, b'dml-broker-v1\0'+challenge)` → `AUTH proof` → expect `AUTHENTICATED b''`. Bounded by `broker_timeout` (default 3s) via `Clock.wait()`. Cleans up writer on any failure.
- `ready(config, clock)`: `connect()` + `PROBE` → expect `READY b''`; returns `bool`, swallows `OSError/ValueError/ProtocolError/EOFError/TimeoutError` → `False`.
- `close_writer()`: `close()` + bounded 1s `wait_closed()`.

### `interactive_access/protocol.py` — terminal framing
- Transport: `HEADER = struct !BBI` = `version=1, kind, length`; `MAX_PAYLOAD 65530`, `JSON_LIMIT 1024`.
- `Type`: `OPEN/STDIN/RESIZE/CLOSE/OPENED/STDOUT/EXIT/ERROR`, handshake `CHALLENGE/AUTH/AUTHENTICATED/PROBE/READY`, workspace `HELLO/WORKSPACE_READY/FILE_REQUEST/FILE_RESULT/FILE_CHUNK/FILE_END/PTY_* /WORKSPACE_STATE/CANCEL`, `SSH_OPEN=48/SSH_READY=49`. Workspace types share framing but terminal sessions never accept them.
- `encode/parse_json/dimensions/ssh_open/decode_header`: duplicate-key rejection (`object_pairs_hook`), exact key-set checks, `OPEN {columns 1-500, rows 1-300, shell:'default'}`, `RESIZE {columns,rows}`, `SSH_OPEN {version:1, public_key:'ssh-ed25519 …' ≤1024, generation 1-2³¹}`.
- `Parser.feed()`: single bounded partial record, handles split/coalesced TCP/WSS chunks; `eof()` errors on trailing bytes.
- `read_record/write_record()`: `readexactly` with `EOFError` vs `ProtocolError` distinction; `write_record` does `drain()` + `await sleep(0)` so probes/cancellation get a turn during continuous output.

### `interactive_access/workspace_protocol.py` — workspace metadata
- Kept separate so workspace clients cannot change terminal semantics. `METADATA_LIMIT 16KiB`, `CONTENT_CHUNK_LIMIT 32KiB`, `TEXT_FILE_LIMIT 2MiB`, `DIRECTORY_PAGE_LIMIT 200`.
- `metadata/metadata_bytes`: bounded JSON object, duplicate-key + non-UTF8 rejection. `path()`: relative only, no `/`-prefix, no `\`/`NUL`, no empty/`.`/`..` segments, `≤64` parts, `≤1024B`. `request_id()`: `1-64` ASCII alnum/`_-`. `pack_chunk/unpack_chunk()`: `len(id)+id+BE32 seq+data` without JSON/base64. Policy (paths, Docker IDs) is **not** enforced here — `WorkspaceSession` on Worker is the enforcement point.

### `interactive_access/session.py` — admission + relay
- `Capacity`: atomic `acquire/release` on event loop; default 1 browser slot. `BUSY` → `ERROR {code:BUSY}` and return. Separate `_ssh_slots` dict keyed by `id(config)` (default 8) so VS Code parallel connections never steal the browser PTY slot and vice versa.
- `run_session(reader,writer,config,capacity,clock)`: sets 64KiB/16KiB write-buffer limits; bounded `open_timeout` wait for first record; dispatches:
  - `SSH_OPEN` → release browser slot, delegate to `run_ssh_session()` (logs its own summary).
  - `HELLO` → delegate to `run_workspace_session()` (logs its own summary, suppresses duplicate generic line).
  - `OPEN` → validate `dimensions(opening=True)`, `connect()` to broker, forward `OPEN`, expect `OPENED`, reply `OPENED {session_id:uuid4, protocol:'terminal-stream-v1'}` then `incoming()`↔`outgoing()` relay: `STDIN/RESIZE/CLOSE` → broker (validates `RESIZE` dims, empty `CLOSE`); broker `STDOUT/EXIT` → client (validates `EXIT {code:-255-255, reason}`, rewrites reason to `'exited'`); `CLOSE` waits `shield(outgoing_task)` bounded by `broker_timeout` so `EXIT` is not dropped by teardown race. Any other kind → `ProtocolError`/`OSError`. Outcomes: `EXIT/BUSY/DISCONNECTED/PROTOCOL_ERROR/TIMEOUT/UNAVAILABLE/SHUTDOWN`; always sends `ERROR {code}` on failure, `CLOSE` to broker, closes both writers, releases capacity, logs `runtime session outcome duration input output` (no payload).
- `run_workspace_session()`: validates `HELLO {protocol:'workspace-stream-v1'}`, `connect()`, forwards `HELLO`, expects `WORKSPACE_READY`, relays both directions through `forward()` with per-direction allowlists (`client_record`: `FILE_REQUEST/FILE_END/CANCEL/PTY_OPEN/PTY_RESIZE` = metadata-checked, `FILE_CHUNK` = `unpack_chunk`, `PTY_STDIN ≤65530`, `PTY_CLOSE/CLOSE` empty only; `server_record`: `FILE_RESULT/FILE_END/PTY_OPENED/PTY_EXIT/WORKSPACE_READY/WORKSPACE_STATE/ERROR` + `FILE_CHUNK` + `PTY_STDOUT`). Counts `input/output/file_ops/pty_events`, tracks `last_client/last_server/close_side`; broker-EOF without client `CLOSE` → `WORKSPACE_BACKEND_EOF`; logs `workspace_reject/workspace_drain_failed` warnings + one summary line, never payload bytes.
- `run_ssh_session()`: validates `SSH_OPEN`, `connect()`, forwards `SSH_OPEN`, expects `SSH_READY` within `ssh_handshake_timeout` (10s), replies `SSH_READY`, then **framing-to-raw transition**: two `read(65536)` tasks relay opaque SSH bytes both ways with awaited writes + `sleep(0)`; half-close propagates (`write_eof`), cancellation independent per direction; `SSH_BUSY/SHUTDOWN/DISCONNECTED/PROTOCOL_ERROR/TIMEOUT/UNAVAILABLE` outcomes with `ERROR {code}` and `sent/received` counts.
- `clock.py`: injectable `Clock.now/sleep/wait` (`ensure_future` + timer race) so tests expire waits without wall-clock sleeps.

### `interactive_access/main.py` — gated service
- `Service.accept()`: `run_session()` per connection, tracked in `tasks` + `session_tasks`.
- `Service.health()`: bounded 3s read to `\r\n\r\n`; `GET /health/live` → 200; `GET /health/ready` → `200 if await ready() else 503`; else 404; minimal `{"status":"ok"/"unavailable"}` JSON.
- `update_readiness()`: `healthy=await ready()`; if healthy and `terminal is None` → `start_server(accept, host, port)`; if unhealthy and `terminal` open → close listener, cancel all `session_tasks`, `wait_closed`. `watch_readiness()` polls every 1s. `start()` does initial `update_readiness()` then starts health server; `stop()` cancels monitor, closes servers, cancels tasks. `main()` wires `SIGTERM/SIGINT` to graceful `stop()`.
- Effect: terminal port is dark until the broker proves itself, and existing sessions are torn down the moment the broker stops proving itself.

### `Dockerfile` + runtime contract
- `FROM python:3.11.14-slim-bookworm@sha256:…`, `WORKDIR /service`, copy only `interactive_access`, `USER 10001:10001`, `HEALTHCHECK` polls `http://127.0.0.1:9002/health/ready`, `CMD python -m interactive_access.main`. No dependencies (`requirements.txt` = stdlib only).

### `test/fake_broker.py` (test-only, never shipped)
- Local PTY substitute proving framing, shell env/cwd, resize, cancellation, reaping: same `CHALLENGE/AUTH`, `PROBE→READY`, `OPEN→OPENED` then `/bin/sh -i` in `pty.openpty()` with `TIOCSWINSZ`, `STDIN/RESIZE/CLOSE→EXIT`, `STDOUT/EXIT` relay, process-group `SIGKILL` cleanup. Production Docker exec/GPU/isolation/placement/snapshots are later acceptance gates.
