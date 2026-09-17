# Changes made for interactive access infrastructure

## Application services

- Added `Headscale_Management`, a private FastAPI service for single-use device
  enrollment, exact Headscale key/node association, endpoint registration and
  leases, generation fencing, readiness probes, revocation and exact-ID cleanup.
- Added explicit SQLite migrations, foreign keys, WAL, atomic transactions,
  durable idempotency/replay records and consistent pre-migration backups.
  Temporary enrollment keys are encrypted and erased after use or revocation.
- Added Ed25519 tickets bound to user, resource generation, service and Gateway.
  Tickets admit one session; authorization leases renew separately with bounded
  deadlines. Signing supports bounded public-key rotation.
- Added `Gateway`, a FastAPI binary WebSocket-to-TCP relay. It checks Origin and
  ticket claims, atomically claims access through management, and connects only
  through a private userspace Tailscale SOCKS5 sidecar. It enforces connection,
  frame, queue and time limits, applies backpressure, and cleans up on disconnect,
  revocation, failed renewal and shutdown. Logs exclude credentials and data.
- Added restricted, idempotent Gateway bootstrap that reuses persistent enrollment
  and Tailscale identity across deployments.

Scheduler application routes, Worker execution and workload image behavior are
outside this infrastructure phase. Scheduler's future authorization/lifecycle
responsibilities are documented in `docs/interactive-access-contract.md`.

## Development and CI testing

- Extended the existing Python 3.11/3.12 unit matrix with both new services while
  retaining Scheduler, Docker Image Builder and Worker jobs.
- Added a separate real-network E2E job on GitHub-hosted Ubuntu 24.04. It uses
  pinned Headscale 0.29.3 and Tailscale 1.102.3 images, disposable certificates,
  temporary credentials and an isolated private DERP fixture. Production
  configuration, Docker Hub credentials and paid accounts are not required.
- Added real enrollment/key-expiry, binary routing, owner/path/signature/audience
  rejection, replay, policy isolation, ordinary-node connectivity, replacement,
  revocation, offline/outage and durable-restart scenarios. Fixture controllers
  enforce explicit user permissions and are excluded from production images.
- Added restart-order/failure/locking tests, repeated disposable deployments and
  sidecar replacement checks. Deliberate failure verifies nonzero exit,
  sanitized diagnostics/JUnit and project-scoped cleanup.
- Added the network test as a deployment prerequisite. The existing registry-
  dependent Docker Hub test retains its original main/master/manual conditions;
  it is expected to skip on the feature branch.
- Pinned runtime/test requirements and public image digests, added Docker build
  exclusions and ignored generated artifacts, caches and local database files.

## Deployment code

- Added `deploy/interactive/compose.yaml` for management, migration, Tailscale,
  bootstrap and Gateway. It uses stable named volumes, private control networking,
  least-privilege secret mounts, actual healthchecks, resource limits and rotating
  logs. Gateway ingress binds to loopback; management/SOCKS/LocalAPI stay private.
- Added protected-file secret provisioning, configuration examples, the required
  policy and a Caddy routing example. Existing Headscale remains a separate
  systemd service; deployment code does not reset or restart it.
- Updated `restart.sh` to serialize deployments, validate storage/configuration,
  build before interruption, retain database/Redis state, migrate explicitly,
  bootstrap idempotently and require actual readiness. Failures return nonzero
  and report partial updates without deleting persistent state.
- Updated Scheduler Compose infrastructure with an external named Postgres volume
  and database/Redis healthchecks. Added a separate one-time backup/offline-copy
  migration script that retains original storage.
- Added `run.sh` as the canonical Mac deployment script. It preserves the supplied
  Builder/UI/Worker steps and changes Scheduler deployment to follow main and use
  `sudo -n env REQUIRE_INTERACTIVE=1`. GitHub Actions continues to call the Mac's
  installed `$HOME/Desktop/github-deploy/deploy.sh`.

## Applied VM changes and validation

- Provisioned the stable `dml-control` network, protected secrets/environment and
  persistent service volumes. Credentials are outside Git and were not printed.
- Updated installed Caddy routing: public Scheduler/Gateway and Headscale
  coordination use existing HTTPS port 443 through sslh/Caddy. Public Headscale
  administrative routes return 404. Verified administrative TLS is bound to
  private bridge `172.19.0.1:8444`, with a restricted persistent firewall rule.
  No cloud-console port change was needed.
- Provisioned a dedicated Headscale operator and explicit service-role policy
  preserving ordinary-node connectivity. Config/policy checks and a consistent
  backup preceded the authorized one-time daemon restart. All 99 original node
  IDs remained present. The administrative key has a 90-day lifetime; replace
  its protected file before expiry and recreate management to reload it.
- Backed up and migrated Postgres to `scheduler-postgres-data`, retaining the
  source anonymous volume and protected SQL backup.
- Two actual deployments passed readiness; Gateway node 163 stayed unchanged.
  Scheduler public HTTPS returned 200. A temporary real endpoint validated public
  Caddy WSS, exact binary relay with an API-issued ticket, replay denial and exact
  cleanup. Its temporary node, containers and disposable state were removed.
- GitHub-hosted unit and real-network checks passed for the implementation,
  including the deliberate-failure/cleanup verifier. Example evidence:
  [Actions run 35174338416](https://github.com/aorko01/Distributed_ML_Training_scheduler/actions/runs/35174338416).
  The cleanup commit receives its own branch CI checks. Registry and automatic
  external deployment success are not inferred from a feature-branch run.

## Repository cleanup

Removed completed compatibility/host smoke experiments, saved compatibility
output, host/status reports, the duplicate Mac deployment example and the
redundant migration README. Removed generated local E2E output and Python/test
caches from the feature workspace. Remaining documentation/configuration examples
describe development, CI, integration or deployment requirements. The original
user plan and historical review were preserved. Tracked removals remain recoverable
from Git history; generated files were moved outside the repository for recovery.

Install `run.sh` as `$HOME/Desktop/github-deploy/deploy.sh` on the Mac before
enabling the new automatic deployment. The user will merge the branch into main;
the actual post-merge registry gate and Mac deployment must still be observed.
Native SSH, terminal UI/framing, HTTP apps, production interactive scheduling,
HA and arbitrary cross-machine/NAT validation remain future work from `plan.md`.
