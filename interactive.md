# Interactive Sessions: From Runtime Create to SSH / VS Code / Web Editor Access

This document explains the **architecture** of interactive sessions — how a
user's "Create workspace runtime" click becomes a live GPU container reachable
from the **browser editor** and from **native VS Code Remote-SSH**. No code,
only the wiring between components and why each hop exists.

## 1. Components and their roles

```
 Browser UI / VS Code + dml-ssh (client machine, public internet)
        | HTTPS (Scheduler)                  | WSS (Gateway)
        v                                      v
 Scheduler (control plane)  <--->  Management (Headscale_Management)
   - identity, ownership,           - enrollments, resources, grants,
     assignment, health gates         tickets, probes, leases
        | Worker heartbeat / claim          | WSS dial authorization
        v                                   v
 Worker host (Ubuntu + Docker)      Gateway (public WSS -> private tailnet)
   - workload container (dml, /workspace, GPU)
   - sshd on workload 127.0.0.1:2222 (no published port)
   - broker (Unix socket, runtime-bound Docker-exec bridge)
   - Access container (byte relay, no Docker socket)
   - Tailscale sidecar + endpoint (tailnet TCP 9000 only)
```

| Component | Owns | Does NOT own |
|---|---|---|
| **Scheduler** | Users, workspaces, revisions, runtimes, generations, owner checks, Start/Stop/status, SSH capability pinning, single-use connection grants | Tailnet keys, tickets signing, byte relay |
| **Management** | Endpoint enrollment, resource versioning, grant issuance (`purpose=browser\|ssh`), ticket signatures, probes, session leases | Scheduling decisions, image builds |
| **Gateway** | Public `wss://…/v1/connect/<resource>/<service>` entry, ticket authentication, tailnet dial via private SOCKS5, blind byte relay | Destination selection (resolved from fresh verified membership, never client input) |
| **Worker** | GPU host, 3 labelled containers (workload / Access / sidecar), broker, sshd lifecycle, tmpfs keys, health reporting, exact-ID cleanup | User authentication, ticket issuance |
| **dml-ssh CLI** | Login, key generation, `~/.ssh/dml-config` + `known_hosts` pinning, per-connection `ProxyCommand` grant fetch + `SSH_OPEN` handshake | Any server state; it only caches scoped tokens and per-runtime keys |
| **VS Code Remote-SSH** | Standard OpenSSH client; opens `/workspace` as `dml` | Knows nothing about tickets — `ProxyCommand dml-ssh proxy …` hides that |

Key design choice: **SSH reuses the existing immutable `workspace` service on
tailnet port 9000**. No second resource, no port 22, no public SSH listener.
Browser and SSH differ only by grant `purpose` (session length) and by the
first control record after Gateway authentication (`OPEN` vs `SSH_OPEN`).

## 2. Phase A — Create and pin the runtime

1. User presses **Start** in the browser. Scheduler checks: active user,
   owns workspace, has an `IMAGE_READY` revision, no other live runtime for
   that workspace, admission enabled, idempotency key fresh.
2. Scheduler pins a **server-owned immutable launch spec**: resource profile
   (CPU/RAM/disk/GPU), `access_service = workspace|terminal`,
   `application_protocol = workspace-stream-v1|terminal-stream-v1`, plus
   `ssh_capable = true` **only if** all three hold:
   - operator flag `INTERACTIVE_SSH_ENABLED=1`, AND
   - ready revision carries SSH image profile (`io.dml.vscode-ssh-profile=v1`), AND
   - editor enabled. Old images stay browser-only with an actionable
     "rebuild image" message. The browser can never set this flag.
3. Scheduler creates the runtime row: `QUEUED`, `generation = N+1`
   (fencing token — every Start bumps it, invalidating old grants/sessions),
   `ssh_status=provisioning`, `ssh_ready=false`. Generation is the single
   anti-replay identity for everything downstream.

## 3. Phase B — Worker builds the runtime (ordered startup)

The Worker that claims the assignment builds three containers with exact
`dml-<assignment>` labels and performs steps **in order**:

```
verify image digest/label/UID 10001/workdir /workspace
  -> create workload (network none or operator-approved bridge, GPU UUID, keepalive)
  -> create tmpfs sshd keys/config (never snapshotted, never leave host)
  -> start + smoke sshd inside workload (127.0.0.1:2222 only)
  -> start + smoke broker (Unix socket /run/dml-interactive/<id>/ + token)
  -> start Access + Tailscale sidecar, join tailnet via one-time key (wiped after join)
  -> enroll endpoint, publish Serve 127.0.0.1:9000 -> 9000, Gateway probe
  -> report health {workload, broker, access, endpoint} + SSH host key to Scheduler
  -> READY + ssh_ready=true
```

Notable wiring:

- **Broker** is pre-bound to the server-side runtime/generation record. It
  never accepts a container ID, command, user, or path from the client.
  Browser PTY uses Docker-exec `/bin/sh`; SSH uses a fixed bridge to the
  workload's sshd. Browser slot (`busy`) and SSH slots (bounded count,
  `INTERACTIVE_SSH_CAPACITY`, default 8) are independent — SSH bursts don't
  block the editor.
- **Access** holds no Docker socket, no credentials, no endpoint state. It
  authenticates to the broker over the mounted Unix socket (challenge/HMAC)
  and relays bytes.
- **Late sshd death** flips `ssh_ready=false` / `sshd-failed`: new SSH grants
  are denied but healthy browser sessions survive. Worker restart, lease
  expiry, Stop, or time-up withdraws the endpoint, cancels relays/execs,
  stops exact containers, revokes grants, wipes tmpfs.

## 4. Phase C — Discovery: is this runtime SSH-ready?

```
Browser polls runtime status -> sees ssh_capable + ssh_ready + generation
  -> reveals "Connect with VS Code" block with copy-paste command
```

- `GET /interactive/runtimes/<id>/ssh-info` (owner-checked, browser or CLI
  token) returns only: runtime/generation/state, `ssh_user=dml`,
  `workspace=/workspace`, public host key + fingerprint, capability/status.
  No secrets, Docker IDs, tailnet addresses, or paths.
- If `ssh_capable=false` or `ssh_ready=false`, the UI shows why (old image,
  still starting, sshd failed) instead of a broken command.

## 5. Phase D — One-time client setup (dml-ssh login + configure)

Runs on the **user's laptop**, once per machine + once per runtime:

1. `dml-ssh login --scheduler https://…` — username/password over Scheduler
   HTTPS, once. Returns a short `interactive:ssh`-scoped access token + a
   rotating hashed refresh token. The browser JWT is never pasted into SSH.
   Scoped tokens cannot hit general user routes; logout/disabling revokes them.
2. `dml-ssh configure <runtime-id> --scheduler https://…` (copied from UI):
   - fetches `ssh-info`, fails fast on dead/non-SSH runtimes,
   - generates `~/.ssh/dml-<runtime-id>` (Ed25519) if missing,
   - **pins the server-reported host key** into `known_hosts` (changed key
     within the same generation = hard failure, never bypass),
   - writes one `~/.ssh/dml-config` stanza:
     `Host dml-<runtime>-g<generation>` with `User dml`,
     `IdentityFile`, and
     `ProxyCommand dml-ssh proxy --runtime <id> --generation <n> --public-key <pub>`.
   - Old `dml-*` stanzas/keys are pruned so VS Code lists exactly one host.
3. User in VS Code: **Remote-SSH → Connect to Host → `dml-…-g…` → Open Folder
   `/workspace`**. From here VS Code behaves as if the container were local:
   terminals, debugger, extensions, port-forwarding, and `scp` all work; edits
   are instantly visible in the browser editor (same live `/workspace`).

## 6. Phase E — Every SSH connection (per-leg fresh grant)

Each VS Code connection (and each port-forward/scp leg) repeats this
invisibly via `ProxyCommand` — nothing to refresh manually:

```
OpenSSH -> dml-ssh proxy -> Scheduler POST /ssh-connection (scoped CLI token)
  -> Management POST /internal/v1/access-grants {user, resource, generation, service=workspace, purpose=ssh}
  -> single-use ticket (60s admission) + wss_url (no-store, never in URL/logs)
  -> Gateway WSS authenticate {type, ticket} (5s) -> ready
  -> dml-ssh sends SSH_OPEN {version:1, public_key, generation}
  -> Gateway dials tailnet TCP 9000 (fresh membership + probe verified)
  -> Access -> broker: generation check + install Ed25519 key into authorized_keys
  -> broker SSH_READY -> raw SSH byte relay (OpenSSH <-> sshd as dml)
```

Why each check exists:

- **Owner + generation + service check (Scheduler, before grant):** only the
  owner of a live `READY` + `ssh_ready` runtime of the current generation gets
  a ticket. Burst window (5 grants / 10 s) tolerates VS Code's parallel
  install+exec legs without opening abuse.
- **Re-check after grant (Scheduler):** if Stop/generation change/SSH-health
  loss raced the grant call, the just-created grant is revoked and the request
  fails with 409 instead of leaking a ticket into a dead runtime.
- **Ticket properties (Management):** Ed25519-signed, audience=gateway,
  `purpose=ssh`, bounded session max (default 4 h vs 30 min for browser),
  60 s admission expiry, 15 s renewable authorization lease, one-time claim.
  Stop/revocation/version change/idle/lease-loss kills the session immediately.
- **Generation check (broker):** the client's claimed generation never selects
  a container — it is compared against the server-side assignment. Mismatch =
  `UNAVAILABLE`.
- **Host-key pinning (client):** prevents silent re-pointing at a different
  container/generation.

The browser web-editor path is identical except: `POST
…/workspace-connection` with a browser token, `purpose=browser`, then
`HELLO {"protocol":"workspace-stream-v1"}` → `WORKSPACE_READY` → file/PTY
records, or legacy `OPEN` → `terminal-stream-v1` PTY. Same Gateway, same
resource/service/port, shorter lifetime.

## 7. Phase F — End of session

- Closing VS Code ends that TCP relay; the workload keeps running. Reconnect
  fetches a new grant automatically.
- **Stop** in the UI (or 4 h SSH cap / health loss / lease expiry) closes all
  SSH relays, revokes grants, removes exact containers/keys/sockets, and
  freezes `ssh_*` flags. Old `dml-…-g…` hostnames stop working by design —
  configure the next runtime anew.
- Logs/metrics carry only IDs, outcomes, durations, and byte counts — never
  passwords, refresh tokens, tickets, keys, or forwarded bytes.

## 8. How to explain it in one minute

> Create pins a generation and an SSH capability. The Worker builds a GPU
> workload with a loopback-only sshd plus a generation-bound broker, fronted
> by an Access relay and a tailnet endpoint on the existing workspace service.
> The UI advertises SSH only when READY and ssh_ready. The user logs in once
> with a scoped CLI token, pins the host key, and gets a single VS Code host
> whose ProxyCommand fetches a fresh single-use SSH grant per connection.
> Gateway authenticates the ticket and dials the tailnet; the broker checks
> the generation, installs the ephemeral public key, and switches to raw SSH.
> No public SSH port ever exists; Stop or a generation bump cuts everything.
