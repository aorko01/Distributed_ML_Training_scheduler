# Portable real network E2E

Install `requirements.txt` and invoke from repository root:

```bash
python test/interactive_e2e/run.py
```

Requires Docker Compose/OpenSSL; no local Headscale/Tailscale installation, paid
account, repository secret, registry login or production URL. Images are pinned
by tag and manifest digest. Tested pair is Headscale 0.29.3/Tailscale 1.102.3;
`compatibility-evidence.json` records actual M1 correlation/transport/policy proof.
`compatibility.py` independently reproduces that disposable spike.

The driver connects only to ingress network (gateway/controller), with no Docker
socket or tailnet interfaces. Management/control, coordination/private DERP and
fixture orchestration have separate networks. Echo/canary listeners bind only
loopback in each sidecar namespace, exposed to the tailnet by real TCP Serve.
Private embedded DERP has a container-DNS SAN certificate trusted by clients;
public maps/fetching are disabled. All transport uses real userspace SOCKS5.
Policy grants only gateway-to-endpoint TCP 9000. Tests attack peers via SOCKS
with listening/Serve evidence and positive controls, never substitute ping.

Fixture controller has explicit A/B user/resource permissions. Its orchestration
and role-test proxy are packaged only in the test image, never production apps.
Runtime keys are issued through actual management API, consumed by real clients,
confirmed against exact key association, registered/leased and probed by gateway.
No READY DB injection or mocked enrollment is used. Driver test hooks request
only four allowlisted disposable stop/start actions; host harness scopes commands
to this unique project. These hooks are not production authentication modes.

Suite covers real enrollment/key reuse/key expiry, exact concurrent binary A/B
routing, owner/path/signature/audience/replay rejection, bridge bypass denial,
peer/reverse/port policy, preserved ordinary-node connectivity with role isolation,
generation replacement, local and live revocation,
exact-ID cleanup isolation with unrelated sentinel, offline endpoints, management
and Headscale outages and durable replay after restart. Before tests the harness
performs repeated migrate/bootstrap/app recreation on persistent volumes and
replaces sidecar to prove gateway uses current namespace/identity.

Unit restart verification uses fake Docker/curl, no infrastructure:

```bash
python -m pytest test/interactive_e2e/test_restart_unit.py -q
bash -n restart.sh deploy/migrate-postgres-storage.sh
shellcheck restart.sh deploy/migrate-postgres-storage.sh
```

Harness captures bounded sanitized logs/status, whitelisted node/policy metadata
and JUnit before unconditional project-scoped teardown. Secrets/DB/state/private
keys/environment/raw inspect/key listings are never artifacts. Skipped required
tests and zero selected tests fail. Primary pytest failure survives secondary
diagnostic/cleanup failure. `--cleanup --project interactive-<id>` is the CI fallback.

CI also runs `--deliberate-failure`, selecting the intentional failure and checking
its JUnit, diagnostics and absence of surviving project containers. This avoids
treating an infrastructure failure as evidence that assertion failure is handled.
Artifacts have seven-day retention. GitHub job runs Ubuntu 24.04/Python 3.11 on
push/PR/manual events, including forks, with contents:read and no secrets.

Single-runner baseline does not prove forced DERP, cross-machine NAT or installed
production Headscale/WSS configuration. Native SSH, browser terminal UI/framing,
HTTP apps, interactive scheduling and HA remain separate future work.


## Explicit installed-host check

After production cutover, an operator can build the fixture image and run:

```bash
docker build -f test/interactive_e2e/fixtures/Dockerfile -t interactive-host-fixture .
sudo python3 test/interactive_e2e/host_smoke.py \
  --fixture-image interactive-host-fixture \
  --public-url https://scheduler.example.com \
  --origin https://scheduler.example.com
```

This opt-in check uses the installed private management API and protected controller
file. It creates one isolated endpoint with a unique identity, confirms/registers
and leases it, waits for the actual Gateway probe, requests a ticket, verifies
public trusted WSS and exact binary echo, rejects replay, and revokes the exact
endpoint before removing only its disposable project/state. It never mounts Docker
into the driver. Keys, tickets and data are not printed. This is an operational
check, never part of the secretless portable CI suite. Override `--management` or
`--controller-file` if the operator's private placement differs.
