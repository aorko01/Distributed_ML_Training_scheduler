# Interactive infrastructure implementation status

The implementation follows `plan.md`, milestones M1–M6. Production cutover is a
separate operational gate; existing Headscale and Scheduler state have not been
changed by the disposable validation.

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

- GitHub-hosted [Actions run 35172507482](https://github.com/aorko01/Distributed_ML_Training_scheduler/actions/runs/35172507482)
  validated implementation commit `f230a102ff4582d4fde97ab024040b80b2a4dac5`
  and passed every unit job (all five components, Python 3.11/3.12) and the Ubuntu
  network job, including the full suite, deliberate-failure verifier, cleanup and
  sanitized artifact upload. This was a feature-branch push, not an actual fork PR.
  The credential-dependent builder registry job and production deploy were skipped.

## Remaining gates

- Before production: validate the Caddy/policy follow-up in GitHub Actions, apply
  the separately validated host proxy/policy candidates, provision the admin key
  and protected environment, deliberately migrate Scheduler Postgres storage and
  validate reverse-proxy WSS. See `host-cutover-proposal.md`.
- Verify the external-machine post-push hook invokes this Linux checkout with
  `REQUIRE_INTERACTIVE=1`. The existing Actions deployment targets an external macOS
  script and does not establish this host's hook.

## Host inspection

Installed Headscale advertises `https://headscale.zulfiker.xyz` and listens on
`127.0.0.1:8080`. A Docker-container HTTPS health probe failed with a TLS internal
alert. Its file policy path is empty. `/etc/distributed-ml/interactive.env` is absent.
The user reports the push-to-main trigger runs on another machine; its invocation
of this Linux checkout remains unverified. The private `dml-control` network and
stable offline secrets have now been provisioned. A root-only Postgres SQL backup
completed without stopping containers. Installed Caddy/Headscale configuration and
Scheduler database storage are still unchanged.
These are unresolved operational prerequisites, not reasons to weaken TLS or reset
state. `restart.sh` rejects missing configuration/storage before changing services.

Native SSH, terminal framing/UI, HTTP applications, interactive scheduling, HA and
cross-machine/NAT/forced-DERP validation remain future work as specified in the plan.
