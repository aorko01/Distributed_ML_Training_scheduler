# terminal-stream-v1 and runtime-bound broker v1

Version 1 uses a six-byte `!BBI` header: version (1), type, big-endian
payload length. Maximum payload: 65,530 bytes; complete record: 65,536.
Headers and payloads may span arbitrary TCP/WebSocket reads. No heartbeat or
automatic shell reconnection is allowed. Gateway remains a blind byte relay.
WSS ticket authentication and Gateway `ready` JSON precede binary records.

| Type | Direction | Payload |
| --- | --- | --- |
| 1 OPEN | client → endpoint | exact JSON `{columns,rows,shell:"default"}` |
| 2 STDIN | client → endpoint | opaque bytes |
| 3 RESIZE | client → endpoint | exact JSON `{columns,rows}` |
| 4 CLOSE | client → endpoint | empty |
| 5 OPENED | endpoint → client | `{session_id,protocol:"terminal-stream-v1"}` |
| 6 STDOUT | endpoint → client | opaque PTY bytes |
| 7 EXIT | endpoint → client | `{code,reason}` |
| 8 ERROR | endpoint → client | `{code}` |

JSON is UTF-8, at most 1,024 bytes, no duplicate or unknown keys. Dimensions
are integers (not booleans), columns 1–500, rows 1–300. OPEN is required first
and exactly once. CLOSE terminates input. Invalid framing, partial-record EOF,
or state violations close the session with `PROTOCOL_ERROR`. Other public codes:
`UNAVAILABLE`, `BUSY`, `TIMEOUT`. No backend error text is returned.

Limits: OPEN 5 seconds, broker connect/auth/readiness 3 seconds, write drain
5 seconds, one session per endpoint by default (operator maximum 16).
StreamReader limit 65,536, transport high watermark 65,536 and low 16,384;
relay reads/writes one record at a time with awaited drain, without queues.
Health HTTP request headers are bounded to 4,096 bytes and 3 seconds.

## Internal Unix-socket broker protocol

The socket and token file are operator selected absolute paths under
`/run/dml-interactive/<runtime-id>/`. Neither protocol nor configuration can
select a container, arbitrary command, user, environment, host path or mount.
The token is random ASCII with at least 32 bytes, in a regular non-symlink file,
mode 0400/0440/0600/0640 (never world readable or group writable).
Access must run with the UID/GID that can read that secret and connect to the
runtime socket. Each broker connection gets a fresh authentication challenge:

1. Broker sends type 16 CHALLENGE: 32 random bytes.
2. Access sends type 17 AUTH: HMAC-SHA256(token, `dml-broker-v1\0` + challenge).
3. Broker verifies with constant-time comparison, consumes that challenge once,
   sends type 18 AUTHENTICATED (empty). A replay on another connection fails.
4. Access sends type 19 PROBE (empty), broker sends 20 READY (empty), then EOF;
   or Access sends OPEN and uses STDIN/RESIZE/CLOSE, receiving
   OPENED/STDOUT/EXIT. Authentication cannot be repeated in a session.

The broker's OPENED metadata is replaced by Access's opaque session ID; broker
ERROR details are never forwarded. EOF cancels the PTY; CLOSE does the same.
Future Worker broker: choose the container exclusively from its server-side
runtime/generation record. Docker exec uses Tty=true, AttachStdin/Stdout/Stderr=true,
Privileged=false, verified image WorkingDir (fallback /workspace), configured
workload User and bounded TERM/HOME. No Docker socket enters Access.

## Threat model and phase boundary

Browser users are untrusted, including the owning user. Access compromise can
control only that runtime's default shell through the mounted socket. Do not
share sockets/tokens between runtimes. Workload files/dependencies are untrusted
and remain in a separate image/network namespace without credentials.
The Unix mount boundary and Worker generation fence are essential; the token
alone is not host isolation. Keep all existing Origin, grant, lease, revocation
and tailnet policy requirements in interactive-access-contract.md.

The test broker creates a local PTY in a test process only. It is never copied
into Access's image and does not prove Docker exec, GPU access, multi-host NAT,
or workload placement. The Worker runtime phase implements a separate production Docker-exec broker and a short Connect verification; Save remains disabled. See interactive-runtime-operations.md for rollout gates.
