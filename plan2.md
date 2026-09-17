# Interactive runtime scheduling, Worker execution, and connection verification

Status: implementation plan only. This document contains instructions for the
agent that will implement the next phase. No application code is implemented by
writing this plan.

## 1. Required outcome and scheduling decisions

The interactive image build/push phase already exists. Extend it so an owner can
start an `IMAGE_READY` revision, wait for placement and startup, and click Connect.
After a real connection through Gateway to the workload succeeds, the frontend
shows **Connected successfully**. The connection is a short verification session
for now; no editor or terminal interface is required.

Use the user's requested scheduling order:

1. Eligible VRAM-estimation work.
2. Interactive access, if the entire Worker is idle and its GPU is free.
3. Batch training, including retries.

These are priorities among eligible assignments for a requesting Worker, not
preemption rules. Existing work is allowed to finish. An ineligible interactive
request must not prevent batch placement on a Worker that cannot run it.

An interactive assignment exclusively reserves the **whole machine**, including
machines with several GPUs. It may start only when there are no active, claimed,
starting, resuming, estimating, or cleaning-up batch jobs and no other interactive
assignment. Reserve the machine when Scheduler claims the interactive request,
before the Worker pulls its image. Keep it reserved through startup, execution,
stopping, and cleanup. No further estimation, training, resume, or interactive
assignment may enter that machine during this interval.

The Worker stops polling for new work from the moment it accepts an interactive
assignment until cleanup and Scheduler release are acknowledged. It continues
sending heartbeats and receiving lifecycle instructions through heartbeat replies.
Do not implement permanently separate interactive and batch Worker pools: an idle
Worker can transition between these uses.

Keep scheduling deliberately small: priority order, eligibility, and existing
batch fit rules. No preemption, backfilling, fair-share scheduler, fractional GPU
allocation, automatic migration, or new resource-request workflow. Isolate this
policy from assignment safety and execution so later priority/placement changes
can use the same claim protocol, lifecycle, Worker, and UI.

The editing machine is not a runtime host. Docker, NVIDIA GPU, registry, and
multi-host verification belong on separately provisioned Ubuntu hosts or CI.
Do not require production services or credentials on the editing machine.

## 2. Read and preserve the existing contracts

Read these files before implementation:

| Area | Existing files and relevant behavior |
| --- | --- |
| Previous phase | `plan.md`, `changes2.md`, `docs/interactive-images-operations.md` |
| Image/workspace state | `Scheduler/app/models/interactive_workspace_model.py`, `services/interactive_workspace_service.py`, `api/interactive_workspace_route.py` |
| Actual batch selection | `Scheduler/app/services/job_service.py`; `scheduler_service.py` mainly provides statistics |
| Worker state/telemetry | `Scheduler/app/models/worker_model.py`, `services/worker_service.py`, `services/watchdog_service.py` |
| Worker execution | `Worker/main.py`, `api.py`, `executor.py`, `hardware.py`, `job_state.py`, `runtime_config.py`, `telemetry.py` |
| Transport | `docs/interactive-access-contract.md`, `docs/terminal-stream-v1.md` |
| Endpoint management | `Headscale_Management/headscale_management/{main,schemas,enrollment_service,endpoint_service,grant_service}.py` |
| Gateway/Access | `Gateway/interactive_gateway/main.py`, `Access_Container/interactive_access/` |
| Frontend | `UI/User/src/pages/InteractiveDetails.tsx`, `UI/User/src/services/interactive.ts` |
| Existing verification | `test/interactive_e2e/`, Scheduler/Worker/Access unit tests, `UI/User/test/interactive.test.tsx` |

Do not reuse the batch output-directory mount for interactive workloads. Batch
execution currently uses `--gpus all`; exclusive interactive placement must cover
all batch launch paths, including VRAM estimation and persisted-job resume.

Current batch claims are not sufficient as an exclusivity lock: estimation does
not change the pending status, selection uses unlocked queries, and Redis holds
the job-to-Worker mapping. `Job.device` stores a GPU model, not a Worker ID. Add
durable assignment accounting rather than inferring idleness from those fields,
GPU utilization alone, or the node statistics page.

The current heartbeat loop skips heartbeats when telemetry is paused. Change
pause to suppress new work only; it must not suppress heartbeats, lease renewal,
health checks, or stop instructions.

The production Docker-exec broker does not exist. The broker in
`test/interactive_e2e/fixtures/terminal_broker.py` is a local-PTY test fixture and
must not be used as the production workload implementation.

## 3. Architecture and boundaries

Keep the existing data path:

```text
Browser -- HTTPS --> Scheduler: owner actions and short-lived access grant
Browser -- WSS --> public Gateway
                    --> private tailnet TCP 9000
                    --> per-runtime Access service
                    --> authenticated runtime-specific Unix socket
                    --> trusted Worker Docker-exec broker
                    --> PTY inside the workload container

Worker -- outbound authenticated HTTPS --> Scheduler: claim/heartbeat/events
Scheduler -- private controller API --> Headscale Management
Worker endpoint sidecar -- outbound --> Headscale / tailnet transport
```

Scheduler is the privileged management controller. Workers receive only their
own endpoint enrollment material, never Headscale administration credentials or
the management controller secret. Workers do not need inbound public API ports.
Keep `Worker/server.py` a local dashboard/control service, not a remote exec API.

One runtime consists of the workload container, the generic Access container,
and a Tailscale endpoint sidecar. Only Access and the sidecar share a network
namespace. The workload uses its own isolated network namespace. Only trusted
Worker-side components access the Docker daemon.

## 4. Separate the replaceable scheduling policy from the mechanism

Introduce a small Scheduler scheduling package, for example:

- `app/services/scheduling/types.py`: Worker snapshot, candidate, decision,
  assignment kind, and capacity requirements.
- `app/services/scheduling/policy.py`: the three ordered selection strategies.
- `app/services/scheduling/claims.py`: transactions, locks, reservations,
  idempotency, leases, and lifecycle fencing.
- `app/services/interactive_runtime_service.py`: runtime lifecycle and owner API.
- `app/services/interactive_controller.py`: management enrollment and endpoint
  reconciliation, independently of scheduling decisions.

The policy receives a read-only Worker/capacity snapshot and queries for eligible
candidates. It returns a candidate or no work. It does not commit a transaction,
call Docker/Headscale, renew leases, release capacity, or shape frontend state.
Repository helpers may perform bounded database queries; do not load the entire
queue into memory to make the interface appear pure.

The claim layer validates the returned candidate and atomically records its
reservation. Every launch path uses this layer. A different policy implementation
must be selectable in one factory/configuration location without modifying the
Worker dispatch, container lifecycle, heartbeat protocol, or frontend.

### 4.1 Initial policy

For every new-work request:

1. Reject placement if Worker registration is stale, inventory is incomplete,
   the Worker is paused/draining/testing, reconciliation is pending, or it has
   an interactive reservation/uncertain cleanup. This guard runs before priority
   selection, including before VRAM estimation.
2. Consider VRAM estimation using the existing eligibility rule and FIFO order.
   When comparing available VRAM with other Workers, exclude offline, paused,
   full, testing, and interactive-reserved Workers. Otherwise an unavailable
   high-VRAM Worker can unnecessarily block estimation everywhere else.
3. Consider the oldest compatible queued interactive runtime, ordered by
   `(created_at, id)`, only if all whole-machine idle checks pass. Skip incompatible
   requests rather than blocking the queue behind them.
4. Consider batch retries, then runnable training, retaining their current fit
   and ordering rules. Both belong to the third priority tier.
5. Return no work when nothing fits; provide a bounded retry delay.

Priority applies to new placement. A persisted batch attempt that is still
authoritatively assigned is recovery of existing work, not a way to bypass
priority and create an unrecorded assignment. Its reservation prevents interactive
placement while it is being reconciled or resumed.

Interactive eligibility requires both Scheduler and Worker agreement:

- No unreleased assignment of any kind on that Worker.
- No local active/pending/resuming batch work or unresolved local journal entries.
- Fresh GPU inventory reports an available compatible physical GPU and no
  non-baseline activity on any GPU. Include running GPU process information;
  an idle-looking utilization sample is not enough.
- Supported image platform, NVIDIA runtime, and sufficient configured host
  RAM/CPU/disk budget, including image-pull headroom and Access/sidecar overhead.
- Interactive runtime feature enabled and host prerequisites validated.

Initially expose one operator-configured interactive resource profile. It gives
one physical GPU by UUID and bounded CPU/RAM/PIDs/writable storage; the remaining
GPUs on the machine stay unassigned. No GPU requirement estimator is needed for
interactive images. GPU models/minimum memory can be profile constraints.
CPU-only operation is permitted in explicit test fixtures, not as a silent
production fallback when no GPU is available.

GPU busy thresholds and tolerated baseline driver/display usage are host policy,
not assumptions hidden in the UI. Unknown inventory means ineligible. Document
that these Worker hosts must not run unmanaged training processes outside Worker
control; recheck local activity immediately before launch and abort if it changed.

### 4.2 Minimum concurrency protection

Add a durable assignment record for **every** estimation, batch, and interactive
claim. This is necessary accounting for exclusivity, not an advanced scheduler.

Use one Worker-row lock for its claim/release operations. In a consistent lock
order, lock Worker, then candidate/job/runtime rows. Select candidates with
PostgreSQL `FOR UPDATE SKIP LOCKED`, recheck state and assignment uniqueness,
write the assignment and state change, and commit once. On contention/conflict,
retry a bounded number of times or return no work. Do not hold transactions
across Redis, Docker, registry, or management network calls.

Batch reservations begin before returning a claim, so a batch image pull also
blocks interactive placement. Estimation must have a unique live assignment even
while its Job status remains `VRAM_ESTIMATION_PENDING`. All candidate queries
exclude already assigned jobs. Keep the Redis mapping as a compatibility/cache
view; Redis loss must not make a machine idle or a job claimable twice.

All Scheduler processes, heartbeat callbacks, resume requests, and watchdogs must
use the same accounting. A process-local mutex alone cannot enforce this.

## 5. Persistent state and migrations

Add `Scheduler/migrations/002_interactive_runtimes.sql` and wire it into the
existing ordered migration runner under its advisory lock. Include indexes,
constraints, migration tests, and matching ORM models. Do not depend on
`create_all` to modify production tables.

### 5.1 Worker execution assignments

Create `worker_assignments` with:

- Opaque assignment UUID; Worker ID; Worker instance/boot UUID; kind
  `vram_estimation`, `batch_training`, or `interactive_access`.
- Exactly one target: batch Job ID or interactive runtime ID.
- Attempt token, request/idempotency key and request hash, timestamps, lease,
  state, and `released_at`.
- For batch, preserve training/retry dispatch information and existing payloads.
- For interactive, store whole-machine exclusivity, allocated GPU UUID, immutable
  launch-spec/profile version, and runtime generation.
- Safe result/failure code and cleanup acknowledgment; no credentials or raw
  terminal output.

Enforce target-kind consistency, unique claim request per Worker instance, unique
unreleased assignment per batch Job, and unique unreleased interactive assignment
per Worker/runtime. Cross-kind exclusion is enforced under the Worker lock; a
unique index on interactive rows alone cannot prevent a concurrent batch claim.

Add protected Worker execution metadata: protocol capability/version, current
instance, last authenticated heartbeat, inventory, paused/draining/reconciling
state. Resolve active assignments from durable rows. If an exclusive-assignment
pointer is also stored for efficient reads, update it in the same transaction
and verify it agrees with those rows.

### 5.2 Interactive runtime

Create a separate `interactive_runtimes` model containing:

- Runtime UUID, workspace ID, owner ID, source revision ID and digest pinned at
  Start, generation, resource-profile version, and request idempotency fields.
- Desired state `RUNNING` or `STOPPED`; observed lifecycle state; assignment ID.
- Created/assigned/ready/stopped timestamps, startup deadline, last accepted
  health report, public failure code and bounded safe detail.
- Management resource ID, enrollment ID, registered endpoint version, and
  reconciliation progress. Resource ID must be unique to the assignment, not
  the reusable workspace ID.
- Internal exact workload/Access/sidecar IDs and cleanup progress, where needed
  for support/reconciliation; do not return these through owner APIs.

Use foreign keys that enforce revision/workspace/owner consistency. Pin the ready
revision at Start even if `current_revision_id` changes later. Do not update the
ready revision's digest, provenance, or build state during execution.

Allow one queued/live/cleanup-pending runtime per workspace, enforced under its
lock and a database constraint. An owner may use separate workspaces on separate
eligible machines; whole-machine exclusivity does not imply a one-runtime-per-user
limit. Stop/failure does not clear the workspace guard while cleanup is uncertain.

Keep old assignment and cleanup records so late messages cannot affect a new
runtime. Generation is allocated under the workspace lock and never reused.
For this phase, failure ends the runtime after cleanup; the owner can Start a
new runtime. Do not introduce automatic migration or replay a once-ready
workload from its original image without the owner requesting a new start.

### 5.3 Lifecycle

```text
QUEUED -> ASSIGNED -> PULLING -> STARTING -> CONNECTING -> READY
   |          |          |          |           |         |
   +----------+----------+----------+-----------+---------+-> STOPPING
                                                               |
                                                  cleanup confirmed
                                                               |
                                                    STOPPED or FAILED

Any assigned state with lost lease/unknown host state -> LOST
LOST -> STOPPING -> STOPPED or FAILED after reconciliation/cleanup
```

QUEUED cancellation can become STOPPED directly because nothing was allocated.
Desired STOPPED wins over any delayed progress report. LOST prevents grants and
holds the machine reservation; it is not proof that containers disappeared.
READY means workload health, Access/broker health, verified membership, current
endpoint lease, and Gateway probe are all satisfied. IMAGE_READY remains only
an image-build state.

## 6. API contracts and authentication

Implement strict request schemas, bounded bodies, real HTTP errors, owner checks,
and idempotent mutations. Do not accept arbitrary image references, commands,
container IDs, tailnet IPs, host paths, or management destinations from browsers.

### 6.1 Owner API

Add routes under the existing authenticated interactive API:

| Method/path | Behavior |
| --- | --- |
| `POST /interactive/workspaces/{id}/runtimes` | Start current ready revision, or an explicitly selected ready revision belonging to this workspace; use the configured profile. Require `Idempotency-Key`; return the runtime, normally 202. |
| `GET /interactive/workspaces/{id}/runtime` | Current/latest runtime or null, with public state, phase, profile, timestamps, and safe failure reason. |
| `POST /interactive/runtimes/{id}/stop` | Idempotently set desired STOPPED, cancel queueing or start cleanup; return 202 while stopping. |
| `POST /interactive/runtimes/{id}/connection` | Check ownership, desired state, current assignment/generation, fresh health and READY; obtain a single-use management grant. |

Start on a non-ready revision returns 409. Another live runtime returns its state
or a clear 409, never a second allocation. Exact Start retries return the original
runtime; reusing a key with different input returns 409. Cross-owner IDs return
404. Keep the existing workspace DELETE's pending-build cancellation semantics;
do not silently convert it into runtime deletion.

Connection returns only the public `wss_url`, admission ticket/expiry, runtime ID,
generation, `tcp-stream-v1`, and `terminal-stream-v1`. Construct the URL from a
configured public Gateway origin and assignment resource/service; never from a
browser-supplied origin or a Worker IP. Use `Cache-Control: no-store`; redact
tokens in HTTP tracing. Rate-limit grant creation and allow only one in-flight
connection verification per UI instance. An expired/consumed ticket requires a
new explicit connection request.

### 6.2 Worker API

Use an authenticated internal Worker API, for example `/internal/workers/v1`,
with register/reconcile, claim, heartbeat, assignment event/result, endpoint
bootstrap, and cleanup acknowledgment operations. Add a typed `SchedulerAPI`
client instead of mixing interactive data into `JobExecutor`'s old dictionary.

Provision a distinct high-entropy service credential per Worker through protected
files. Scheduler binds it to the registered Worker identity; a body `worker_id`
is not authentication. Support rotation/revocation. Builder, Worker, Gateway, and
management controller credentials remain separate. Use HTTPS between hosts.

The common claim envelope includes protocol version, kind, assignment ID, attempt
token, Worker instance, lease budget/deadline, and a kind-specific payload. Batch
payloads retain `vram_estimation`, `training`, and `retry` dispatch semantics.
Interactive payloads contain runtime/workspace/revision/owner opaque IDs,
generation, digest reference, GPU UUID, and the bounded launch specification.
Do not include management controller or registry push credentials.

Claims require a client-generated request UUID persisted before sending. A lost
response must be recovered with the same request UUID, not a second claim. Return
the same active assignment on an identical retry, or a terminal/stale result once
released; never mint a replacement under an old key. Conflicting reuse is 409.

Heartbeat includes current instance, active/pending assignment IDs and tokens,
execution mode, local reconciliation status, timestamped GPU/host inventory,
runtime phase, health flags, and bounded event sequence numbers. Its response
contains lease decisions and exact assignment stop/reconcile instructions.
It must not assign new work as a side effect of heartbeat.

Every progress/result/bootstrap/cleanup operation validates Worker identity,
instance, assignment ID, attempt token, generation, and legal state transition.
Expired attempts cannot revive themselves. Exact duplicates are idempotent;
older sequence numbers cannot regress lifecycle state.

Cleanup acknowledgment remains possible after expiry through a restricted
reconciliation operation authenticated as that Worker. It can acknowledge removal
of the exact old assignment, including a prior instance, but cannot renew it,
mark it READY, complete its job, or release a newer assignment. This avoids
leaving crashed Workers permanently blocked by the normal stale-attempt checks.

### 6.3 Batch compatibility is part of the exclusivity change

Move existing batch and estimation claim/result/resume paths through the shared
assignment layer. Workers keep the existing training executor; only dispatch,
accounting, and fenced result handling need change. Preserve builder callbacks.

Do not leave old routes as a bypass. Audit `/jobs/pull_job`, `/jobs/resume`,
`/jobs/save_vram_estimation`, `/jobs/update_job_to_runnable`, `/jobs/mark_completed`,
`/jobs/mark_failed`, and `/jobs/upload_output` (which can mark a job completed).
For assignment-managed work, they must require the matching authenticated fence
or reject the operation. The old unauthenticated Worker register/heartbeat routes
must not modify protected assignment/instance state or clear an interactive hold.

For the initial deployment, use a coordinated drain/upgrade: disable new claims,
finish/reconcile old in-flight batch jobs, upgrade Scheduler and Workers, then
enable interactive placement. Do not infer ownership of legacy running jobs from
GPU model or an absent Redis key. Legacy Workers remain ineligible for interactive
work until they use the new protocol and complete reconciliation.

Record batch/estimation results and release only after that attempt's containers
and local launch tasks are stopped. A result callback must not create an idle
window before its container exits. If results and release are separate, keep the
assignment live until the cleanup acknowledgment. A pending result retry keeps
its reservation. Prevent the old Redis watchdog from independently requeueing
assignment-managed jobs; reconcile their durable assignments instead.

When a managed batch lease expires, block new work on that Worker and reconcile
the old attempt before release/retry. Estimation failure returns to estimation
eligibility after confirmed cleanup; it must not become training-ready without
a valid report. Training infrastructure failure retains its existing retry
behavior, but only after the previous attempt is confirmed stopped. Stale result
callbacks never overwrite a newer attempt's result.

## 7. Worker execution coordinator and polling behavior

Add a shared execution coordinator, for example `Worker/execution_state.py`,
used by the job loop, batch executor, resume scanner, interactive manager, and
heartbeat collector. It owns one lock and a durable local assignment journal.
Do not overload the batch-only `running_job.json` format with interactive state.
Use SQLite transactions or equivalently crash-safe atomic persistence under the
Worker's state directory. Acquire a host service lock so two Worker processes
cannot control the same host/Worker identity concurrently.

Track these local modes:

- `RECONCILING`: inventory/old assignment handling; no new claims.
- `AVAILABLE`: normal priority-based new-work polling is permitted.
- `BATCH_ACTIVE`: existing batch concurrency and slot limits apply; interactive
  acceptance is impossible while any active/pending/resuming slot exists.
- `INTERACTIVE_RESERVED`: claim persisted; no job polling or resume scans.
- `INTERACTIVE_ACTIVE`: lifecycle/health processing and heartbeats only.
- `CLEANING` / `UNCERTAIN`: no new claims until exact cleanup/release is resolved.

Do not implement this by setting `max_concurrent_jobs=0`: runtime config clamps
values, and slot counts do not represent machine exclusivity. Add an explicit
`may_request_work` guard checked before resume scans and before HTTP claims.

Serialize local claim acceptance and batch/resume reservations through the same
coordinator. Permit at most one outstanding claim request. If an interactive
response arrives while an unexpected local batch/resume task exists, do not
launch it; report the conflict and reconcile the already-created Scheduler
reservation. Never discard the response and continue polling.

Continue heartbeats during long image pulls, endpoint enrollment, active access,
user pause, and stopping. Keep Docker/pull waits out of the heartbeat thread and
out of the execution-state mutex. When an interactive runtime holds the machine,
report that explicit mode rather than pretending its unused GPU memory is
available for scheduling. Resuming polling requires both confirmed local cleanup
and Scheduler release acknowledgment; network failure leaves the Worker blocked.

## 8. Pull and launch the actual interactive workload

Implement a separate `Worker/interactive/` package for manager, journal, Docker
operations, endpoint lifecycle, broker, and cleanup. It must not call the batch
output monitor, batch command runner, or training completion APIs.

### 8.1 Startup sequence

1. Persist the accepted assignment and exclusive mode before any slow operation.
   Verify the lease and local idle conditions. Start runtime health/lease tracking.
2. Pull the exact Scheduler-recorded `repository@sha256:...`. Validate the registry
   against operator configuration. Never fall back to a tag or `latest` if the
   digest pull fails. Use protected pull-only registry credentials when required.
3. Inspect the pulled image and verify digest/platform, configured User/WORKDIR,
   required `/bin/sh`/keepalive support, and absence of incompatible image-declared
   volumes. Reject declared volumes for this initial runtime, because Docker can
   otherwise create anonymous mounts and hide editable files from future saves.
   Distinguish a manifest-list digest from its platform image when verifying it.
4. Recheck lease/cancellation, whole-machine idleness and selected GPU UUID, then
   create/start the workload by the verified image ID associated with that digest.
   Persist its exact Docker ID immediately; deterministic labels let startup
   reconciliation find objects created just before a journal write crashed.
5. Start the runtime-bound host broker and validate a real Docker-exec open/close
   smoke session. Do not advertise readiness merely because the container exists.
6. Start the pinned endpoint sidecar and Access container, with the mount/network
   boundaries below. Verify broker and Access health before publishing Serve.
7. Request endpoint bootstrap through Scheduler, join the endpoint, and report
   phase/health through the assignment API. Scheduler confirms/registers it and
   waits for the Gateway probe. Only then can the runtime become READY.

Use bounded pull/start deadlines with configurable defaults (for example 30
minutes for large CUDA pulls and 2 minutes for endpoint startup). Transient pull
retries may retry the same immutable digest within the same assignment/lease;
limit attempts and back off. Authentication, unsupported image, disk, platform,
or capability failures need actionable public error codes, not endless retries.
Check cancellation before and after each side effect, including a pull that
finishes after Stop. Never create containers after the assignment loses authority.

### 8.2 Workload isolation and lifecycle

- Override both image entrypoint and command with a fixed, tested keepalive
  supported by the accepted workload images. Disable image healthcheck commands;
  use Worker-owned health checks. Do not automatically run the image's training
  command. Do not interpret user-supplied names as shell commands.
- Respect the validated image User and WORKDIR, with `/workspace` fallback when
  WORKDIR is unset. Empty User follows an explicit documented operator policy;
  do not silently substitute the host UID or grant privileged exec. Validate
  shell/workdir permissions before reporting ready.
- Use `--init`, default seccomp, dropped capabilities, no-new-privileges, PID,
  CPU/memory/swap, and writable-storage limits. Give only the selected GPU UUID;
  never `--gpus all` for the interactive workload.
- No host/Docker socket mounts, host PID namespace, privileged mode, published
  workload ports, or access/registry/service credentials in workload environment.
  Default workload network is `none`.
- Keep editable files in the workload's writable layer; do not mount over
  `/workspace`, WORKDIR, or the user's home. Do not automatically commit anything.
- Do not use automatic container restart or `--rm`; explicit lifecycle ownership
  and exact-ID cleanup must survive a Worker crash and be inspectable.
- Label all three containers with management component, Worker, assignment,
  runtime, workspace, revision, generation and opaque owner IDs. Use IDs, not
  user names, for Docker names, paths, and cleanup selectors.

Make writable-layer quota support a runtime-host preflight requirement. For
example, Docker's `overlay2` size option requires XFS with project quotas; do not
assume it works on every Ubuntu installation or Docker storage backend. Report
the actual supported setup and fail preflight if the configured limit cannot be
enforced. Do not reformat or migrate a developer's Docker storage automatically.
See [Docker storage options](https://docs.docker.com/reference/cli/docker/container/run/#set-storage-driver-options-per-container).

Account separately for pulled image layers and bounded container logs; a writable
layer limit does not limit either. Check disk headroom, limit concurrent pulls,
and retain cached images safely without global prune commands.

### 8.3 Access unit and endpoint secrets

Use the existing pinned Tailscale version/digest from `deploy/interactive/` and
the published, digest-pinned Access image. Run Tailscale in userspace mode. Only
the sidecar has its local API socket and ephemeral state; Access must not receive
them. Access has no Docker socket and no Headscale/service credentials.

Create a per-assignment directory under `/run/dml-interactive/<opaque-id>/`.
Use that same safe ID as `ACCESS_RUNTIME_ID` so Access's current path validation
accepts the socket/token paths. Mount only this runtime's broker directory
read-only into Access, with directory ownership/modes allowing its numeric UID
to traverse/read/connect. Never mount the parent containing other runtimes.

Keep endpoint enrollment material in root-owned tmpfs files, not environment,
Docker command arguments, image layers, logs, or ordinary journal columns. Join
through Tailscale's local API over its protected Unix socket, following the
existing bootstrap pattern. Wipe the one-time key after confirmed membership.
Do not expose a SOCKS proxy from the endpoint namespace; the Gateway's existing
private SOCKS dialer remains the transport client.

Expose only tailnet TCP 9000 with a fixed Serve target `127.0.0.1:9000`. The Access
health port 9002 stays loopback-only. Do not copy the fixture's canary port 9001
or fixture control server into production. Verify the exact Serve command/local
API against the pinned release in integration tests; raw TCP forwarding is
documented in [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve).

## 9. Implement the production Docker-exec broker

Implement `docs/terminal-stream-v1.md` exactly, reusing its parser and limits
without importing a test fixture into production. Each Unix socket is permanently
bound to one trusted assignment/container record. No message chooses another
container, command, user, directory, environment, mount, or GPU.

Authenticate each connection using the existing fresh 32-byte challenge and
HMAC contract. A PROBE checks the bound running workload, current local lease,
and broker health; it must not create a shell or consume terminal capacity.

On OPEN, create and attach a real Docker exec PTY in the workload with
`Tty=true`, stdin/stdout/stderr attached, `Privileged=false`, and the validated
workload User/WORKDIR. Initial shell is fixed `/bin/sh`, with bounded TERM/HOME.
PTY output is one raw stream, not Docker non-TTY multiplexed output. Forward
resize using exec-resize. Return OPENED only after attachment succeeds and the
exec is running. Start with one active terminal per runtime, matching Access.
See [Docker exec](https://docs.docker.com/reference/cli/docker/container/exec/).

Use bounded buffers, timeouts, and backpressure for both directions. A stalled
session must not block heartbeat or runtime teardown. Never forward PTY bytes to
batch job logs or telemetry; log IDs, safe outcomes, durations and byte counts.

Implement explicit terminal cleanup. Closing an attach socket or killing a local
`docker exec` client is not proof that the process inside Docker stopped. Track
the exec ID and its inspected process identity. On CLOSE/disconnect/revocation,
close streams, terminate the session process and descendants with a bounded
grace period, then verify exec exit and reap broker resources. Any host-side
signalling must verify container/cgroup membership and process identity, use
PID-reuse-safe handles where available, and never trust a PID sent by Access.
Docker's API provides exec inspection/resize; do not invent an exec-kill endpoint.
See [Docker Engine exec API](https://docs.docker.com/reference/api/engine/version/v1.40/#tag/Exec).

For this phase, if the broker cannot prove a timed-out session was terminated,
mark the runtime unhealthy and stop the exact workload container as the bounded
fallback. This loses that ephemeral runtime and must be reported, not silently
presented as a successful session close. A normal verification CLOSE must keep
the workload running. Validate normal and hostile cleanup against real Docker,
including shells that ignore signals and spawn children, before enabling Connect.

## 10. Scheduler-to-management reconciliation and readiness

Add a typed management client with configured private base URL, controller secret
file, TLS/CA settings, bounded timeouts and redacted errors. Reuse existing
management schemas; do not make Scheduler sign tickets or trust Worker-supplied
tailnet addresses.

Process one persisted lifecycle step at a time. Use compare-and-swap state/version
checks or a database-claimed reconciliation lease so multiple Scheduler processes
cannot run contradictory steps. Perform remote calls outside transactions, then
revalidate the assignment before accepting the result. Compensate late side
effects by revoking their exact endpoint/enrollment; Stop always wins.

The handshake is:

1. Scheduler enrolls `role=endpoint`, `identity=<assignment resource UUID>`,
   `generation=<runtime generation>` with a stable enrollment idempotency key.
2. Persist the enrollment ID before returning its key through the authenticated,
   assignment-bound Worker bootstrap operation. Pending redelivery can use
   management's same-key replay; do not persist the plaintext key in Scheduler.
   Once membership is confirmed the key is no longer replayable: return an
   already-enrolled state and verify the existing sidecar, not a replacement key.
3. Worker joins its sidecar, verifies broker/Access health, enables Serve, and
   reports readiness for confirmation. Management independently observes the
   exact node associated with the issued key; hostname/IP reports are not proof.
4. Scheduler confirms the enrollment and registers resource ownership with
   `service=terminal`, `protocol=tcp-stream-v1`, `port=9000` and exact generation.
5. Existing Gateway probing observes that registration/version. Scheduler waits
   for management READY while continuing endpoint leases only on fresh Worker
   health. Then it transitions the runtime to READY.

Important existing API details:

- Management's resource-lease response includes endpoint state/version. Use it
  to observe READY; there is currently no generic resource GET status route.
  Add a narrowly authenticated read route only if actually needed.
- Re-registering the same enrollment does not reset an expired endpoint lease.
  Treat that as a failed assignment, clean up, and require a new runtime/generation.
- Enrollment `UNKNOWN_RESULT` is not permission to generate a second key. Follow
  the existing bounded ambiguity/expiry handling and exact-ID cleanup contract.
- Resource DELETE is not generation-qualified. The unique per-assignment
  resource ID prevents delayed cleanup from revoking a newer runtime on the
  same workspace. Never reuse that resource ID.

Gateway's current probe is a TCP dial, and Serve can accept while its backend
is unhealthy. Therefore Worker health must gate Serve and Scheduler leases/grants.
On workload/broker/Access failure, Worker withdraws Serve promptly and reports
failure. Scheduler stops grants and revokes the exact resource; existing sessions
close through management revocation/session lease loss. Do not weaken Gateway
Origin checks, tailnet policy, ticket replay prevention, or destination validation.

For connection grants, Scheduler rechecks the owner, desired state, fresh runtime
health, current assignment and generation before calling management with
`authorized=true`. Recheck after the remote response; if Stop/replacement won,
revoke the returned grant and do not return its ticket. Keep this separate from
the image-builder credential and image-building endpoints.

## 11. Leases, stops, crashes, and releasing the machine

Start with configurable Worker heartbeat 5 seconds, assignment lease 45 seconds,
and reconciliation interval 5 seconds. Validate that any mutable heartbeat setting
leaves renewal margin; do not permit dashboard settings to exceed the lease.
Management endpoint/session leases retain their own documented values and must
be renewed frequently enough; they are not interchangeable with Worker leases.

Scheduler uses authoritative persisted lease deadlines. Worker uses a monotonic
local deadline derived conservatively from the request-send time and accepted
renewal budget, with safety margin. Reject late responses that would extend a
lease after the local deadline, and ignore out-of-order heartbeat responses.
Wall-clock changes and a delayed response must not resurrect execution.

Stop sequence, also used for startup failure and lease loss:

1. Set desired STOPPED/stop cause and block grants/claims immediately.
2. Scheduler requests exact resource/enrollment revocation; Worker concurrently
   withdraws Serve and stops accepting broker sessions. Neither waits forever
   for the other network path before making local access unavailable.
3. Close/reap terminal sessions; stop/remove Access, sidecar, and workload by
   exact IDs with matching labels. Cancel pending launch/pull tasks, and verify
   they cannot create late containers. Handle partially created runtimes too.
4. Remove only this assignment's endpoint state, socket/token directory, and
   secrets. Confirm exact containers and processes are gone. Persist a local
   cleanup tombstone before acknowledging cleanup.
5. Scheduler records cleanup and releases the Worker reservation transactionally.
   Finish management cleanup idempotently; if authorization revocation is not
   yet confirmed, retain the cleanup hold rather than advertise the Worker idle.
6. Worker receives release acknowledgment, clears its active reservation, sends
   an idle heartbeat, and resumes normal priority-based polling.

An unreachable Worker becomes LOST/unschedulable. Never free its machine merely
because heartbeat TTL expired. No replacement interactive runtime is admitted
until cleanup is confirmed. If Docker itself is unavailable, keep the machine
quarantined and surface cleanup pending; do not claim success.

On Worker startup, reconcile the durable local journal and labelled Docker
objects before ordinary polling or persisted batch resume. For initial scope,
do not adopt an old interactive runtime across a Worker-instance change: shut it
down and report the interruption. Keep prior instance/assignment cleanup identity
until the Scheduler acknowledges it. An unknown Scheduler response or corrupted
journal must not trigger broad Docker deletion or a false idle report.

Supply an Ubuntu systemd unit with automatic Worker restart, correct SIGTERM
handling and `ExecStopPost` exact-runtime cleanup. Docker containers can outlive
a SIGKILLed Python Worker, so normal `finally` blocks are insufficient. Use a
bounded cleanup helper based on the journal/labels, plus startup reconciliation;
if cleanup cannot finish, restart in quarantine. Stop admission on service
shutdown before terminating heartbeat. Use a host-level watchdog/supervised
lease guard if the Worker event loop can stall; it must independently withdraw
and stop expired interactive runtimes. Test SIGKILL, hung launch tasks, network
partition, host restart, and Docker restart. This is lifecycle reliability, not
additional placement policy.

A normal browser verification disconnect does not stop the workload or release
the machine. Only Stop, failure, lease loss, or an explicitly configured runtime
maximum lifetime does. Default runtime lifetime can be operator-configured;
document it and expose the deadline if enabled. Do not secretly use browser
disconnect as runtime expiry.

## 12. Frontend: start, status, stop, and a success message

Extend the existing details page/service; keep image state and runtime state
separate. Show Start when an owned revision is ready and no live runtime exists.
Show queued/pulling/starting/connecting/ready/stopping/lost/failed states with
simple text and safe error reasons. Provide Stop for queued or allocated work.
Explain that this phase's runtime is temporary and Stop discards unsaved runtime
changes. The saved source image remains available for another Start.

Poll runtime status while a runtime is live, including READY, so health loss and
external stop appear in the UI. Clean up polling on navigation/logout and prevent
old responses from replacing a newer runtime/generation. Current build polling
alone is insufficient because it stops at IMAGE_READY.

Enable Connect only for a current READY runtime. Implement a small transport
module independently from page rendering:

1. POST the owner connection endpoint and keep the returned ticket in memory.
2. Open the configured public WSS URL without query credentials; set
   `binaryType=arraybuffer`.
3. On WebSocket open, send the exact initial JSON authentication message from
   `interactive-access-contract.md`. WebSocket open alone is not success.
4. Validate Gateway's JSON `ready` with `protocol=tcp-stream-v1`.
5. Send a binary terminal-stream-v1 OPEN for the default shell and fixed valid
   dimensions, for example 80 columns by 24 rows.
6. Incrementally parse binary records across arbitrary WebSocket fragmentation
   and coalescing. Require a valid OPENED for `terminal-stream-v1`. Only now show
   **Connected successfully**: this proves the Gateway path reaches Docker exec
   inside the intended workload, rather than merely opening a TCP proxy socket.
7. Send CLOSE immediately, allow a bounded graceful EXIT, then close WSS. Keep
   the workload allocated. The message is a successful connection check, not a
   claim that an invisible browser terminal remains connected.

Do not send user code/STDIN or render terminal output in this phase. Drain/discard
bounded output during the check so a shell banner cannot deadlock it. Respect the
existing frame/JSON limits, unknown-record rejection and OPEN deadlines. Use a
bounded overall connection timeout, clear connecting state on every exit path,
and close on unmount/logout. Never put tickets in URLs, local/session storage,
analytics, console logs, or error messages.

Display a concise failure message for grant rejection, timeout, stale runtime,
protocol error, or Gateway closure. Require a fresh ticket for Retry; no automatic
ticket replay or unbounded reconnect loop. Clear the success indication when
the runtime/generation changes or becomes unavailable. Keep Save disabled.

## 13. Future compatibility boundary

The user intends to add browser editing and later create an image for batch
training. Preserve these architectural properties now:

- Stable workspace identity, immutable source revisions, separate mutable runtime.
- Real execution inside the workload image with its existing dependencies.
- Workload writable-layer changes remain independent of Access/Tailscale state.
- Runtime/generation fencing and owner authorization have one shared boundary.
- Workload contains no injected service/registry/enrollment secrets.
- Keep `SNAPSHOT` revision origin reserved and record the exact source revision.

Docker commits exclude mounted volume data, which is why the interactive
workspace must remain in the writable layer. See the
[Docker commit documentation](https://docs.docker.com/reference/cli/docker/container/commit/).

Do not implement or write an editor implementation plan, file-editing APIs,
notebook integration, commit/push endpoints, or batch submission from snapshots
in this phase. These are compatibility constraints only.

## 14. Verification required of the implementing agent

### 14.1 Development/CI checks without production infrastructure

Use fake clocks, Docker adapters, management clients, registry clients, and HTTP
transports for meaningful unit/component tests. Keep production secrets disabled.
Run affected Scheduler, Worker, Access and UI suites plus the existing build
regressions. Add a frontend test script that actually includes the new connection
and runtime tests; the existing script targets one specific test file.

Required scheduling/accounting cases:

- All three queues populated: eligible estimation wins; after it finishes,
  interactive wins; batch wins only when higher tiers have no eligible candidate.
- Retry training cannot jump ahead of eligible interactive work.
- Ineligible interactive work does not block an eligible batch job.
- Any active/claimed/pulling/resuming/estimating/cleaning batch work prevents
  interactive placement, even with another completely free GPU.
- Interactive ASSIGNED/PULLING/STARTING/READY/STOPPING/LOST prevents all other
  placement, including estimation, repeated pull requests and old resume routes.
- Concurrent claims on one Worker cannot create a batch/interactive overlap;
  concurrent Workers cannot claim the same estimation job or interactive runtime.
- Lost claim response returns the same assignment; duplicate/stale callbacks,
  clock changes, Redis loss and Scheduler restart cannot clear the wrong hold.
- Switching to a test policy changes candidate choice without changing claim,
  Worker, Gateway or UI contracts. Exclusivity guards still apply.

Worker/controller/UI cases:

- Job-poll and resume-call counts remain unchanged while an interactive
  assignment is held; heartbeat count continues increasing during pull, pause,
  startup, active access, and cleanup.
- Shared local locking prevents resume/claim acceptance races.
- Wrong digest, unavailable private registry, busy GPU, wrong architecture,
  unsafe image volumes, unsupported quota, and exhausted disk fail cleanly.
- Workload/Access/broker startup failures clean only their exact assignment.
- Late Docker completion after Stop cannot create a surviving runtime.
- Enrollment replay/ambiguity, join timeout, stale membership, failed probe,
  expired endpoint, revocation races and management outage fail closed.
- No secret appears in workload inspect/env, public schemas, logs, browser
  storage, socket URLs, or recorded Docker arguments.
- UI cannot report success on WebSocket open or Gateway ready alone. It succeeds
  after valid OPENED, handles fragmented/coalesced records, sends CLOSE, and
  closes the verification socket without stopping the runtime.
- Other owners, stale generations, consumed tickets, and wrong service/Origin
  cannot connect. Stop while a grant is being issued revokes the late grant.

### 14.2 Real PostgreSQL gate

Extend the disposable-schema migration/concurrency harness. Exercise upgrades
from the current schema, assignment uniqueness, simultaneous Worker claims,
batch-vs-interactive claim races, result/stop races, and multiple reconciler
processes. SQLite unit tests alone do not verify row locking or partial indexes.

### 14.3 Docker integration gate on a separate Ubuntu host/CI runner

Extend `test/interactive_e2e/` with a production-runtime scenario using the real
Scheduler claim/controller and real Worker Docker broker. Keep the fake-broker
suite as a protocol test, but do not count it as workload execution acceptance.
Use a small deterministic digest-pinned fixture image and disposable registry
for portable CI. Include real Headscale, Tailscale, Gateway and Access.

Verify image contents and execution identity inside the workload, correct User/
WORKDIR, bidirectional PTY bytes/resize, and actual child-process cleanup. Repeat
connection checks to detect leaked exec sessions. Inspect container boundaries,
health gating, inaccessible canary ports, and absence of Docker/credential
mounts. Kill/restart components and demonstrate exact cleanup without deleting
an unrelated sentinel container, image, volume, or Headscale node.

Assert the user-visible behavior end to end: Start -> queued -> assigned -> ready
-> Connect -> success message -> verification socket closed -> runtime still
running -> Stop -> reservation released -> normal work polling resumes.

### 14.4 GPU and multi-host acceptance on the actual runtime environment

On a GPU-enabled Ubuntu Worker host, use a ready image produced by the existing
builder and pull it by digest. Verify NVIDIA access is limited to the assigned
GPU and the real host's storage/resource limits work. Exercise the priority order
with real queued estimation, interactive, retry and training jobs. Demonstrate
that an active batch job prevents interactive launch and that an active
interactive runtime produces heartbeats but zero new-work polls/assignments.

Run Gateway/control plane and Worker on separate machines/networks. Verify public
HTTPS/WSS from a browser without Tailscale, outbound Worker connectivity, private
management isolation, reconnect/failure reporting, and lease-loss cleanup.
Single-host Docker networking does not prove NAT traversal or cross-host routing.

Clearly separate checks actually run, skipped for missing infrastructure, and
still pending. Do not label production GPU/NAT/registry behavior verified based
on mocked tests or this development machine.

## 15. Implementation order, deployment, and completion checklist

Implement in this order, keeping runtime admission disabled until the pieces are
ready:

1. Assignment/runtime models, migration, constraints and shared claim layer.
2. Isolated three-tier policy; route batch/estimation/resume accounting through
   the common mechanism and close legacy bypasses.
3. Authenticated Worker protocol, idempotent claims, heartbeat decisions, durable
   local coordinator and journal; verify exclusive polling behavior.
4. Interactive digest pull, isolated containers, real Docker-exec broker, and
   local cleanup/lease supervision.
5. Scheduler management client/controller, enrollment/registration/readiness,
   grants, stop/revocation and crash reconciliation.
6. Owner runtime APIs and frontend Start/status/Stop/Connect verification.
7. PostgreSQL and Docker integration, then real GPU/multi-host acceptance.
8. Deployment examples and an operations runbook with the exact commands for
   installing/upgrading/running/checking each component on its actual host.

Document configuration in component `.env.example`/deployment files without
secrets: feature gate, Worker service identity/credential file, Scheduler URL,
management private URL/controller secret, public Gateway WSS origin, Headscale
login/CA settings, pinned Access/Tailscale images, pull-only registry credential
file, runtime state directory, fixed resource profile, GPU policy, heartbeat/
lease/startup deadlines, storage prerequisites, and service supervision.

Do not use `localhost` for cross-host Scheduler/registry/control URLs. Explicitly
document which host owns every path/socket; host Docker bind paths must refer to
the Docker host. For this phase, support a host-installed Ubuntu Worker managed
by systemd; do not imply arbitrary nested-Docker Worker deployments are tested.

Roll out with new-work admission disabled, a database backup and migration,
drained/reconciled legacy batch work, updated Workers, configured management
controller access, published Access image, and validated Worker preflight. Enable
interactive admission only after smoke verification. Rollback first disables new
interactive starts, then stops/drains runtimes and confirms cleanup before using
an older Worker/Scheduler; an older process cannot safely manage live new-format
assignments.

Completion requires all of the following:

- The exact requested priority order and whole-machine exclusivity are enforced
  by Scheduler and Worker, including during claim/pull/stop races.
- Interactive Workers stop requesting jobs and continue heartbeats.
- Workers pull the ready digest and run the actual image with a real broker.
- The existing Gateway connects through the registered endpoint and the frontend
  shows success only after the workload connection check succeeds.
- Stop/failure/crash cleanup releases only the correct resources and cannot
  permit overlapping assignments or revive a stale generation.
- Placement policy can change independently within the stable eligibility,
  reservation, lifecycle and execution contracts. Changing fundamental isolation
  requirements later would intentionally require changing those contracts too.
- No browser editor, snapshot implementation, or automatic image commit is added.
- The implementation handoff lists changed files, test results, deployment steps,
  and runtime-host checks still pending, without claiming they ran locally.
