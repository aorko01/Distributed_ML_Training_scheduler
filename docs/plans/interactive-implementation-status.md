# Interactive infrastructure implementation status

The implementation follows `plan.md`, milestones M1–M6. The authorized production cutover is now applied and verified on this VM.
Existing Headscale nodes and Scheduler data were preserved. External automatic
push deployment remains a separate, pending operational gate.

## Evidence

- M1 passed with real pinned Headscale 0.29.3 and Tailscale 1.102.3. Exact key/node
  association, role tags, userspace SOCKS, TCP Serve and policy denial with working
  positive controls are recorded in `test/interactive_e2e/compatibility-evidence.json`.
- M2–M4 are implemented. Management units: 32 passed locally (policy-preservation follow-up); gateway units: 31 passed.
  Tests cover durable identity/fencing, atomic claims, expiry, cleanup, rotation,
  routing admission and bounded relay authorization/cancellation.
- Restart orchestration: 13 fake-command tests passed. ShellCheck and shell syntax
  passed. Production Compose resolves with placeholder settings. Disposable repeated
  migrations/bootstrap/app recreation preserve gateway identity; sidecar replacement
  recreates the gateway against the current namespace.
- The final full real-network suite passed all 15 tests locally, including outages,
  replacement/revocation, durable replay and unrelated sentinel survival. Evidence
  is in `artifacts/interactive-caddy-policy-validation/` (local, ignored by Git).
- The intentional E2E assertion failure returned nonzero; its verifier confirmed
  retained sanitized diagnostics/JUnit and no surviving project containers.
- M6 operational documentation and future integration contracts are implemented in
  `docs/interactive-access-contract.md` and the service/deployment READMEs.

- GitHub-hosted [Actions run 35174338416](https://github.com/aorko01/Distributed_ML_Training_scheduler/actions/runs/35174338416)
  validated implementation commit `5442dbf90a2e86fa073039fb0071e7c26f1bb776`
  and passed every unit job (all five components, Python 3.11/3.12) and the Ubuntu
  network job, including the full suite, deliberate-failure verifier, cleanup and
  sanitized artifact upload. This was a feature-branch push, not an actual fork PR.
  The credential-dependent builder registry job and production deploy were skipped.

## Host cutover evidence and remaining gate

Caddy now serves trusted public coordination and Gateway WSS on existing public
443. Administrative HTTPS is restricted to the private Docker bridge. Required
policy is applied without deleting existing nodes. Protected secrets/environment
and persistent Postgres storage are provisioned, with original storage and
consistent backups retained. Two real production deployments passed readiness,
preserving Gateway node 163 and all 99 baseline Headscale node IDs.

The operator-only `test/interactive_e2e/host_smoke.py` passed real enrollment,
Gateway probe readiness, public Caddy WSS with an API-issued ticket, exact binary
relay, replay denial and exact temporary-node cleanup. It is separate from portable
CI and its controller credential is mounted only during this explicit operator check.
See `host-cutover-proposal.md` for applied configuration and evidence.

The user supplied the external macOS deployment script. A reviewed replacement is
prepared at `deploy/external-deploy.sh.example`; install it on that machine so the
Scheduler checkout explicitly follows main and runs the protected deployment with
`sudo -n env REQUIRE_INTERACTIVE=1`. Publishing to main and observing the actual
external deployment remain pending. The existing builder Docker Hub gate still
runs only on its original main/manual events; its review-branch skip is expected,
and registry E2E success has not been claimed for this changeset.

Native SSH, terminal framing/UI, HTTP applications, interactive scheduling, HA and
cross-machine/NAT/forced-DERP validation remain future work as specified in the plan.
