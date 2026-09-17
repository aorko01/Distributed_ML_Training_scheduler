# Interactive Gateway

Public WebSocket `tcp-stream-v1` ingress backed by private userspace Tailscale
SOCKS5, with management-resolved destinations and renewable authorization leases.
See the [contract](../docs/interactive-access-contract.md) for handshake, ticket
claims, close codes and integration boundaries.

Install pinned runtime/test requirements and run `python -m pytest test/unit -q`.
Tests require no Docker or live tailnet. Configuration is explicit environment
and secret files; `.env.example` lists required settings. The package never loads
root production `.env`. Production serves HTTPS/WSS through the host reverse
proxy; plain ingress is loopback-published on the sidecar namespace. Configure
exact HTTPS browser Origins; missing Origin requires explicit CLI mode.

Clients send a small first JSON message containing `type: authenticate` and
`ticket: <ticket>` within 5s, receive `type: ready`, then exchange binary frames up
to 64 KiB. Tokens in URL/query strings are rejected. There are no shell commands,
terminal resize frames, SSH/Jupyter/HTTP routing or Docker socket access. Users
cannot select an arbitrary IP/port. Every dial uses private SOCKS5; failure never
falls back to a direct destination socket.

Defaults: 100 total active sessions, 5 per user, 20 authentication phases;
five-second renewals; five-minute idle timeout; maximum admission/session duration
is managed separately by management. Awaited TCP/WebSocket writes apply
backpressure. Uvicorn bounds frame size/queue and disables compression. EOF closes
the whole connection. Transport failure, disconnect, cancellation, lease loss,
revocation, deadlines and shutdown release capacity and attempt idempotent session
release. A failed renewal cannot extend the previous lease.

`python -m interactive_gateway.bootstrap` is a bounded one-off helper. Only it
gets the restricted bootstrap credential, and it uses root LocalAPI access in the
sidecar's private namespace. Gateway receives only its service secret/public keys.
The helper retains request/enrollment metadata and reuses valid persistent node
state on ordinary restart; it never prints enrollment keys or passes them in argv.
Unmanaged running state fails visibly instead of being adopted. Revoked/expired
managed identity needs explicit decommission/replacement, not reenrollment loops.

Readiness checks management/policy, exact own enrollment, fresh membership,
sidecar Running state, SOCKS negotiation and configured Ed25519 public keys. No
worker endpoints are needed. SIGTERM has a bounded grace period; push updates
interrupt live sessions, whose clients must reconnect with a new ticket. Logs
contain outcome/session ID/duration/byte counts, never tickets or forwarded bytes.

See [production operations](../deploy/interactive/README.md) and
[portable E2E](../test/interactive_e2e/README.md).
