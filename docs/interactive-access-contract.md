# Interactive access contract

This phase provides enrollment management and a binary TCP WebSocket gateway.
Scheduler remains the future identity, active-user, ownership and assignment
authority. Tagged membership identifies a service; it does not prove workload
code is trusted. Host owners retain control of local containers and secrets.

## Transport and trust

User clients connect over HTTPS/WSS without joining the tailnet. The initial
protocol is `tcp-stream-v1`. The first WebSocket message is a JSON object with
exactly `type: authenticate` and `ticket: <single-use-ticket>`. It must arrive
within five seconds. Credentials in URLs are rejected. Browser Origin must match
the configured allowlist; absent Origin requires explicit CLI mode. After a
`ready` JSON response, only binary messages are accepted, at most 65536 bytes each.
Frame boundaries have no TCP meaning. TCP EOF closes the whole WebSocket.

Management resolves destinations from fresh verified Headscale membership, never
client-selected addresses. Gateway dials exclusively through its private userspace
Tailscale SOCKS5 listener. No direct socket fallback is allowed. HTTPS verification
is mandatory in deployment; the disposable fixture uses its own trusted CA.

## Internal roles

All internal operations use `/internal/v1` and a separate bearer service secret.
Unknown mutation fields are rejected. Identifiers are opaque bounded strings;
assignment generations are fencing tokens created by the controller. UUIDs
identify enrollments, grants, sessions and claim requests. Times are UTC.

| Operation | Controller | Matching gateway | Restricted bootstrap |
| --- | --- | --- | --- |
| Create endpoint enrollment | yes | no | no |
| Create configured gateway enrollment | yes | no | yes |
| Enrollment status/confirmation | yes | own gateway only | no |
| Revoke enrollment/resource/grant | yes | no | no |
| Register endpoint/renew resource lease | yes | no | no |
| Issue access grant | yes | no | no |
| Fetch probe targets/report exact probe | no | yes | no |
| Claim/renew/release session | no | own sessions only | no |

Gateway never receives Headscale administrative or signing keys. Enrollment keys
are runtime credentials, single-use, five-minute TTL by default; endpoint keys
are ephemeral and gateway keys persistent. Enrollment responses contain the key
only while pending, encrypted at rest. Status responses never contain it. An
idempotency key is scoped to the authenticated role and exact request hash;
changed payload returns 409. Ambiguous key creation is recorded UNKNOWN_RESULT
and must not be retried until its requested TTL bounds the orphan window.

## Lifecycle and authorization

Node identity requires exact issued pre-auth-key ID and expected tags. Hostname
is descriptive only. A resource binds an immutable owner, generation, enrollment,
named service, protocol and allowlisted port. Reassignment creates a new version,
invalidates existing grants/sessions and fences late confirmations, registration,
lease renewals and probes. Revocation denies locally before retryable exact-ID
Headscale cleanup. Durable tombstones prevent late membership resurrecting access.

READY requires registration, a live controller lease, online verified membership
observed in the last 15 seconds and a successful gateway probe for the exact
endpoint version. Membership alone is insufficient. Reconciliation runs every
five seconds; an unavailable Headscale cannot create new READY records.

Controller must make an access decision before requesting a grant. Management
additionally enforces user equals immutable owner and current generation/service.
Tickets use fixed Ed25519/EdDSA signatures, issuer, gateway audience and a `kid`.
Required claims are `iss`, `aud`, `sub`, `jti`, `iat`, `nbf`, `exp`, `resource_id`,
`generation`, `service`, `protocol`. Public-key overlap for rotation is bounded;
private keys never leave management. Default admission expiry is 60 seconds;
an admitted session has a separate absolute deadline of 30 minutes.

Claim consumes the durable grant atomically. A retry using the same gateway and
stable request UUID returns the same session; another UUID cannot replay it.
Gateway reserves each session once so a repeated claim cannot create two relays.
Every five seconds it renews a 15-second authorization lease. Failed renewal
closes by the existing lease expiry; admission expiry does not end a previously
admitted session. Revocation, version change, idle timeout (five minutes), shutdown,
EOF and errors cancel all forwarding tasks and release transports/capacity/session.

## Error contract

Internal statuses: 401 invalid credential/ticket; 403 wrong role/owner; 404
unavailable; 409 conflicting idempotency/stale generation; 410 expired/revoked;
422 invalid endpoint; 503 control service unavailable. Public errors contain no
other user's endpoint/enrollment metadata. WebSocket close codes: 4401 invalid
ticket; 4403 denied; 4408 authentication timeout; 4410 stale/revoked; 1013
capacity/control unavailable; 1011 transport failure.

Log only IDs, outcomes, durations and byte counts. Never log keys, tickets,
authorization headers, secret request bodies or forwarded bytes. Diagnostics
must exclude databases, environment files, keys and raw container inspection.

## Integration boundaries

Future Scheduler checks active user, owner and current assignment; creates a new
generation for reassignment; requests enrollment; supplies the runtime key only
to the intended endpoint; confirms membership; registers its named service;
maintains resource leases; revokes on completion/decommission; requests grants
only after an explicit access decision; delivers the ticket and gateway WSS URL.

Future endpoints consume the runtime key, expose the named service, report through
an authenticated controller and honor decommissioning. Worker/image behavior,
interactive scheduling, terminals, native SSH, HTTP apps, HA and cross-machine
deployment validation remain future work. Portable single-runner tests do not
prove arbitrary NAT connectivity or this host's existing Headscale setup.

The real-network CI suite validates pinned Headscale 0.29.3/Tailscale 1.102.3. Exact image digests are recorded in `test/interactive_e2e/compose.yaml` and the service/fixture Dockerfiles.
