# Headscale Management

Private FastAPI control service for enrollment, verified endpoint registration,
single-use access tickets and bounded sessions. It has no byte-forwarding role.
The [internal contract](../docs/interactive-access-contract.md) defines roles and
future Scheduler integration. No Worker or Scheduler access routes are included.

Install pinned runtime and test requirements in an isolated Python 3.11/3.12
environment. Run `python -m pytest test/unit -q`. Tests inject a clock and
Headscale adapter and use a temporary file-backed SQLite database, never root
production `.env`. Imports do not create schema or read production configuration.

Use `.env.example` as the configuration inventory. Required credentials support
`*_FILE`; direct environment secrets are supported but secret files are preferred.
Administrative HTTPS verifies system trust or `HM_CA_FILE`. HTTP is accepted only
with explicit `HM_ALLOW_INSECURE_TEST=1`, never deployed by the production manifest.
Run explicit migrations with `python -m headscale_management.database` before
`uvicorn headscale_management.main:app --port 8020 --no-access-log`. One application
process and reconciler are supported. SQLite foreign keys, WAL, a five-second busy
timeout and `BEGIN IMMEDIATE` enforce durable atomic transitions.

Defaults: enrollment TTL 300s; resource lease 60s; admission TTL 60s; session lease
15s; session maximum 1800s; reconcile interval 5s; membership/probe freshness 15s.
Only tailnet TCP 9000 and protocol `tcp-stream-v1` are allowed. Enrollment keys
are encrypted with a stable independent Fernet key. Never change that key without
handling pending ciphertext. Confirmed/expired/revoked records erase ciphertext.
Unknown remote creation results are not retried or adopted by heuristics; their
TTL bounds orphan keys. Explicit replacement uses a new generation and request ID.

Membership requires exact Headscale pre-auth-key ID plus expected tags. Ownership
comes from controller, never tags/usernames/hostname/IP. Local revocation is durable
before remote cleanup, and tombstones continue removing late matching nodes.
Cleanup never bulk-deletes users/nodes/keys. Readiness requires schema, secrets,
administrative reads and the required policy; it does not depend on gateway startup.
Successful probes re-read membership/policy so outages cannot publish new READY.

Ticket signing uses Ed25519 with a fixed EdDSA algorithm and `kid`. To rotate,
publish new/current and old/retiring public keys to management and gateway, switch
private signing key and `HM_SIGNING_KID`, then recreate applications. The JSON map
is `{ "<kid>": { "pem": "<public PEM>", "not_after": <optional UTC epoch> } }`.
At most two keys are allowed; retiring-key overlap is at most one hour. Keep old
verification until admission/retry windows end, then remove it. Existing sessions
renew from durable authorization, independent of ticket admission expiry.

Example internal enrollment (placeholder only):

```bash
curl --fail https://management.example.invalid/internal/v1/enrollments \
  -H 'Authorization: Bearer <controller-service-secret>' \
  -H 'Idempotency-Key: <stable-request-id>' -H 'Content-Type: application/json' \
  -d '{"role":"endpoint","identity":"resource-a","generation":"assignment-uuid"}'
```

Do not put real secrets in shell history. Prefer a protected curl header file in
operations. Enrollment responses are sensitive; status responses are redacted.
See [deployment operations](../deploy/interactive/README.md) for state, policy,
restart, backup, decommission and host connectivity gates.
