# Interactive image phase: operations and Worker handoff

This repository can be edited and checked on a machine that never runs the
services. The image builder runs on a separate Docker-enabled Ubuntu host.
No generated config assumes the development machine is the runtime host.
Use routable Scheduler and object-store URLs on the builder; `localhost` inside
a builder container means that container, not the Scheduler machine.

## Implemented boundary

Authenticated owners create an image workspace from a ZIP with requirements.txt
and an allowlisted PyTorch/CUDA base, or from their own image-bearing batch job.
Training success is not required for the latter. Workspaces and revisions have
separate PostgreSQL tables; they never enter batch queues or VRAM estimation.

Revision states: QUEUED → BUILDING → IMAGE_READY, FAILED or CANCELLED. Expired
or released infrastructure attempts requeue with a 45-second exclusion of that
builder and a maximum of three attempts. Heartbeats cannot resurrect expired
attempts. Every callback/log update needs the current builder ID and attempt ID.
Ready revisions and provenance are database immutable on PostgreSQL; ready
callbacks are idempotent only for exactly the same recorded references.

The UI creates and lists workspaces and displays safe build stages, failure,
tag and digest. It uses real APIs and clears details on errors; it has no mock
fallback. Connect and Save are disabled because no production runtime exists.
The fake broker is strictly a test fixture; Access cannot run a workload itself.

## Runtime host configuration

Back up the Scheduler database using the deployment's existing backup procedure.
Apply [the additive migration](../Scheduler/migrations/001_interactive_workspaces.sql)
before an upgraded Scheduler starts. Startup also runs it under an advisory lock;
new tables do not depend on `create_all` altering old production tables. Retain
source jobs while referenced; archive them rather than physically deleting them.

Generate at least 32 bytes of randomness for the builder service credential and
place identical contents in protected, regular, non-symlink files on the
Scheduler and builder hosts. Example on each runtime host (transfer the same
secret through your normal secret deployment system):

```sh
install -d -m 700 /etc/dml/secrets
umask 077
openssl rand -hex 32 > /etc/dml/secrets/interactive-builder
```

Set `INTERACTIVE_BUILDER_SECRET_HOST_FILE` to that host's absolute path, then
use the overlay on each host:

```sh
# Scheduler host
cd Scheduler
docker compose -f docker-compose.yml -f compose.interactive.yaml up -d --build api
# Ubuntu builder host, with .env URLs pointing to the reachable service hosts
cd Docker_Image_Builder
docker compose -f docker-compose.yml -f compose.interactive.yaml up -d --build
```

The builder's Docker Hub username/password stay in the builder's protected
configuration only. Cancellable CLI pull/push authenticates with password on
stdin; its Docker auth config is a private tmpfs (`/run/docker-config`). The
builder requires the host Docker socket; Access and workloads must never get it.
If the interactive credential is absent, batch building continues and the new
internal routes fail closed. Bad credentials never become a user API route.

New uploads accept only two repository-owned PyTorch/CUDA identifiers, returned
by `/interactive/workspaces/base-images`; add another base by updating Scheduler
and builder allowlists together and verifying its digest on the runtime host.
No raw Dockerfile/FROM or arbitrary browser source reference is accepted.
ZIP limits: 64 MiB compressed, 512 MiB expanded, 10,000 entries; reject traversal,
symlinks/special files, duplicate names and submitted Dockerfile/.dockerignore.
The builder verifies actual expanded bytes in addition to ZIP metadata.

## API and build verification

User router `/interactive/workspaces`: from-upload (strict multipart name,
base_image_id,file), from-job (strict JSON name,source_job_id), owner-filtered
list/detail/build-logs, and DELETE to cancel a pending build. Creation requires
`Idempotency-Key` (16–128 alphanumeric/underscore/hyphen characters). A key is
scoped to owner and exact name/source/archive hash; conflicting reuse is 409.
Other users' workspace/job IDs return 404. `/source-jobs` exposes only eligible
owned IDs/names, with no source tag. Build source keys/tags and lease fields never
appear in user responses.

Builder router `/internal/interactive/builds`: claim, heartbeat, ready, failure,
release and logs. All require `Authorization: Bearer <file credential>` and reject
unknown JSON fields. Claim is discriminated by kind=interactive. Heartbeat accepts
revision_id/attempt_id pairs and returns cancel_builds. Ready requires an
attempt-specific tag, canonical image_digest_ref and resolved_base_digest.
Neither ready images nor callbacks use batch scheduling endpoints.

Credential-free development/CI checks:

```sh
python3 -m venv .venv
.venv/bin/pip install -r Scheduler/requirements.txt -r Docker_Image_Builder/requirements.txt -r Access_Container/requirements-test.txt httpx
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest Scheduler/test/unit -q
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest Docker_Image_Builder/test/unit -q
(cd Access_Container && ../.venv/bin/python -m pytest -q)
(cd UI/User && npm ci && npm test && npm run test:interactive && npm run build)
```

Use Node 22 for the UI. Unit tests deliberately disable loading runtime `.env`
credentials. Development checks do not require Docker, GPU, Docker Hub login or
runtime service startup.

On a disposable PostgreSQL test host/database, set DATABASE_URL and run
`python3 Scheduler/test/postgres/check_interactive.py`. It creates and removes
one exact random test schema, tests migration from the older job schema,
concurrent claims, and database immutability. CI runs this with its own database.

On a Docker-enabled Ubuntu test host, run
`python3 test/interactive_e2e/run.py --project interactive-terminal-check` after
installing `test/interactive_e2e/requirements.txt`. The suite runs real pinned
Headscale/Tailscale/Gateway, non-root Access and a separate local PTY broker. It
covers fragmented OPEN/stdin/resize/output/exit, ownership, wrong service, ticket
replay, lateral denial, grant/resource revocation, lease loss, endpoint offline,
access restart and Gateway shutdown. It reaps children and collects sanitized
metadata/count-only logs. It does not prove production Docker exec, GPU access
or multi-host NAT. CI runs this suite; it need not run on the editing machine.

Optional Docker Hub gate on the builder test host (large CUDA layers): set real
DOCKER_HUB_USERNAME/DOCKER_HUB_PASSWORD and RUN_INTERACTIVE_REGISTRY_E2E=1, then
run `python3 -m pytest Docker_Image_Builder/test/e2e/test_interactive_registry_e2e.py --run-real-dockerhub -v`.
It builds both sources, pushes separate tags, pulls by digest, verifies files and
Torch import, and checks absence of access/Tailscale/SSH/Docker artifacts. It
retains exact registry references for deliberate review. CI manual dispatch has
an `interactive_registry` option for this gate. Registry pushes and real network
results must be verified there; passing unit tests is not a registry acceptance
claim.

## Retention and decommissioning

The builder's persistent SQLite `interactive_artifacts` ledger records revision,
attempt, exact tag, pushed digest and accepted callback. Scheduler references
only accepted ready digests. A crash after push but before callback can leave an
orphan; consult this ledger and authoritative Scheduler revision state before
removing anything. A tag is unique to one revision/attempt and never reused.
Keep ready images while any revision/runtime refers to their digest. Docker Hub
layer sharing is safe; remove only an explicitly reviewed recorded tag/digest
through the registry's normal retention mechanism. Do not automate wildcard
registry deletion, global image/volume pruning, or a Headscale reset.

Build stages are bounded and allowlisted because untrusted dependency scripts
can print credentials/URLs. Raw Docker/pip output is used only transiently for
classification; it is not published on this new log route or to service logs.
Failure messages are stable public text. Detailed dependency diagnostics require
an operator-controlled future redaction design rather than exposing raw output.

## Future Worker acceptance requirements

Pull workload by repository@sha256. Label all three containers with opaque
runtime/workspace/revision/owner IDs and assignment generation. Pre-bind the
Worker broker to that runtime record; never select by user names. Mount only its
socket/token read-only into Access. Authenticate broker sessions with the fresh
challenge contract in terminal-stream-v1.md; use constant-time verification.

Workload: configured User/WorkingDir, default-seccomp, drop capabilities,
no-new-privileges, --init, process/memory/CPU/writable-layer limits, explicit GPU
allocation, no host/Docker mounts and default no network. Override the image's
command with an operator-selected stable keepalive. Access shares only the
Tailscale sidecar namespace. Enrollment secrets belong in root-owned/tmpfs secret
files, and ephemeral endpoint state must be destroyed at exact-ID decommission.
Controller must health-gate registration and withdraw Tailscale Serve when Access
is unready; a TCP probe of Serve alone does not certify backend readiness.

Production broker must create/attach/resize/close a PTY with Docker exec using
Tty and attached streams, Privileged=false, configured workload user/workdir and
bounded TERM/HOME. Shutdown, lease loss and generation replacement must close
sessions, kill/reap PTYs, stop exact runtime containers, revoke the exact managed
endpoint and remove only that runtime's socket/token directory.

Reserve SNAPSHOT origin. Saving creates a new immutable revision under an owner
and generation bound idempotent fence: drain terminals, pause/commit workload
only, push through trusted credential-bearing Worker/builder, record digest, then
switch the current revision atomically. Never commit access/sidecar. Do not bind
mount /workspace if relying on Docker commit; mounted-volume data is excluded
([Docker commit documentation](https://docs.docker.com/reference/cli/docker/container/commit/)).
No credentials or sensitive injected env values may enter the workload before
snapshotting. Production placement, exec, GPU/NAT and save tests are the next phase.
