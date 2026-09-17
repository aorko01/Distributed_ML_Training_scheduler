# Interactive image and access-container implementation plan

Status: implementation instructions only. This plan does not add application
code. It is the next phase after the existing Headscale management and Gateway
work already present in this repository.

## 1. Decision and correction to the proposed design

Keep the user's mutable workload image separate from all Tailscale, terminal,
and access dependencies. The intended data path is:

```text
browser terminal
  -> existing public Gateway over authenticated WSS
  -> per-runtime Tailscale endpoint
  -> generic Access_Container on TCP 9000
  -> per-runtime Unix socket
  -> future Worker interactive-exec broker
  -> PTY created inside the actual interactive workload container
```

The proposed `gateway -> individual_access_container -> actual_image` boundary is
correct, but a shared Docker network or shared workspace alone is not sufficient.
A shell in the access container would use the access container's binaries,
Python packages, CUDA libraries, users, and environment rather than those in the
workload image. To make the session behave like a terminal in the submitted
image, the PTY process must be created inside the workload container.

Do **not** solve this by mounting `/var/run/docker.sock` into Access_Container.
That socket is effectively host control. A later Worker component must own the
Docker API and expose only a narrow, runtime-bound exec/resize/close protocol over
a Unix socket mounted into that one access endpoint. This plan defines and tests
that boundary but does not implement Worker placement, polling, or scheduling.

Do not add OpenSSH to user images. For the initial browser terminal, SSH adds a
second identity/key system without solving browser transport. The existing
Gateway already authenticates a single-use ticket and carries an authorized raw
TCP stream through Tailscale. Implement a small framed PTY protocol in the
generic access service. If native SSH is added later, terminate it in the access
unit, never in the workload image.

An interactive container is not a VM: it shares the host kernel and normally has
no init system. The UI may present a VM-like terminal experience, but product text
and documentation must call it an isolated interactive container/workspace.

## 2. Scope and completion boundary

Implement in this phase:

1. A generic, versioned `Access_Container/` image and its terminal protocol.
2. A fake exec broker used to prove that the access service creates and controls
   a PTY behind its narrow Unix-socket contract.
3. A separate interactive-workspace/image model and image-build state machine.
4. User API and UI choices for:
   - a new interactive workspace from an uploaded ZIP and selected base image;
   - an interactive workspace derived from an existing job owned by that user.
5. Building and pushing the resulting workload image to Docker Hub, recording an
   immutable digest, and displaying build state/logs.
6. Unit, component, security, UI, and portable Docker/Tailscale tests described
   below.

Explicitly defer:

- choosing a Worker, GPU, CPU/RAM quota, queue order, preemption, or placement;
- Worker pull/poll logic and the production interactive-exec broker;
- starting/stopping the real workload and access containers on a Worker;
- issuing production terminal grants from Scheduler and enabling a live Connect
  button;
- committing and pushing a modified running container;
- Jupyter, desktop, arbitrary port forwarding, file browser, and native SSH.

This boundary matters: the current phase can finish at `IMAGE_READY` and can
prove Access_Container against a fake broker, but it cannot honestly provide a
live shell in a GPU workload until the Worker runtime phase exists. Do not hide
that gap with a shell in Access_Container.

## 3. Existing code to preserve and reuse

- `Gateway/` already accepts a ticket in the first WSS message, claims it through
  management, resolves a controller-registered endpoint, and relays binary bytes
  to TCP port 9000 through its private Tailscale SOCKS5 listener. Keep it a blind
  byte relay; it must not parse terminal commands.
- `Headscale_Management/` already supports ephemeral endpoint enrollment,
  generation fencing, owner-bound access grants, endpoint leases/probes, session
  leases, and arbitrary service names with protocol `tcp-stream-v1`. Register the
  future resource with service `terminal`, protocol `tcp-stream-v1`, port `9000`.
- `docs/interactive-access-contract.md` remains the transport/authorization
  contract. Add the terminal subprotocol as a new document; do not weaken the
  ticket, lease, revocation, Origin, or tailnet isolation rules.
- `Docker_Image_Builder/` already has claim fencing, heartbeats, cancellation,
  archive extraction, Docker Hub push, build logs, and attempt-specific tags.
  Reuse those mechanisms while giving interactive revisions their own queue and
  terminal state callback.
- `Scheduler/app/models/job_model.py` and job services are batch-oriented. Do not
  make an interactive image enter `VRAM_ESTIMATION_PENDING`, `RUNNABLE`, or
  `IN_PROGRESS`.
- `Worker/executor.py` currently bind-mounts the output directory over the image
  `WORKDIR`. Do not reuse this behavior for a future mutable interactive runtime:
  Docker commits exclude mounted-volume contents, so edits under such a mount
  would be missing from a saved image.

## 4. Target runtime architecture

Treat one interactive runtime as three isolated pieces:

1. **Workload container**: runs the selected interactive image by immutable
   digest. It contains the submitted files and their Python/CUDA dependencies,
   but no Tailscale, SSH daemon, access token, Docker socket, registry credential,
   or Headscale credential.
2. **Access service**: runs the repository's generic Access_Container image. It
   translates the terminal stream into the narrow broker protocol. It has one
   runtime-specific broker socket/token and no Docker socket.
3. **Tailscale endpoint sidecar**: uses the same pinned Tailscale release and
   userspace pattern as the existing interactive E2E topology. It has one
   ephemeral endpoint identity and forwards tailnet TCP 9000 to the access
   service on `127.0.0.1:9000` with `tailscale serve`.

The access service and Tailscale sidecar share a network namespace. The workload
does not share that namespace. Prefer an endpoint unit of two containers over
running `tailscaled` and the access daemon under a supervisor in one container;
this matches the existing Gateway/fixture pattern and isolates credentials and
process lifecycle. “Access container” in product diagrams may refer to this
access unit.

Future runtime launch requirements, to be consumed by the Worker phase:

- pull the workload by `repository@sha256:...`, never only by a mutable tag;
- give every runtime an opaque ID and assignment generation;
- label all containers with the runtime ID, workspace ID, revision ID, owner ID,
  and generation; never discover containers by user-supplied names;
- create a per-runtime directory such as
  `/run/dml-interactive/<opaque-runtime-id>/`, owned by the Worker service;
- bind-mount only that runtime's broker socket and a short-lived broker token
  read-only into the access service;
- pass endpoint enrollment material through a root-owned/tmpfs secret file,
  never an image layer, command-line argument, log line, or user-visible env var;
- use an ephemeral Tailscale node and destroy its state at decommission;
- run the workload without host mounts, the Docker socket, devices other than
  explicitly assigned GPUs, added Linux capabilities, or host networking;
- keep Docker's default seccomp profile, add `no-new-privileges`, drop all
  capabilities unless a documented workload requirement proves one is needed,
  and set PID/process, memory, CPU, and writable-layer limits;
- default the workload to no network. Any later egress/data policy must be an
  explicit product feature, not inherited from the access endpoint;
- use Docker's `--init` runtime option and override the image command with a
  stable keepalive; do not install an init system in the user's image;
- execute the interactive shell as the configured workload user, at the image's
  configured `WORKDIR` (falling back to `/workspace`), with bounded environment
  values such as `TERM=xterm-256color` and `HOME` derived from that user.

## 5. Access_Container deliverables

Create a top-level component following the package isolation used by Gateway:

```text
Access_Container/
  interactive_access/
    __init__.py
    main.py             # listener, connection/session lifecycle, health
    config.py           # strict env/file configuration
    protocol.py         # incremental framed-stream parser/encoder
    broker_client.py    # Unix-socket client; no Docker implementation
    session.py          # capacity, deadlines, cancellation, byte counters
  test/unit/
  test/component/
  Dockerfile
  requirements.txt
  requirements-test.txt
  pytest.ini
  README.md
  .env.example
docs/terminal-stream-v1.md
```

Pin the base image and dependencies. Run as a numeric non-root user with a
read-only root filesystem and a tmpfs for unavoidable transient files. Listen on
loopback TCP 9000 because `tailscale serve` is the only intended ingress. Expose:

- `GET /health/live` on a separate loopback-only health port: process-only.
- `GET /health/ready`: validates configuration, the exact broker socket, token
  file permissions/readability, and a bounded broker readiness exchange.
- raw TCP 9000: one `terminal-stream-v1` session per connection.

Do not include Docker CLI/SDK, an SSH server, Docker/registry credentials, shell
utilities intended for users, or Headscale administrative credentials in this
image. The Tailscale binary belongs in the separate pinned sidecar.

### 5.1 Terminal wire protocol

The existing Gateway is a TCP stream: WebSocket frame boundaries are not
preserved. Define `terminal-stream-v1` as length-prefixed records and implement an
incremental parser that handles records split or coalesced across reads.

Use a fixed header containing a protocol version, one-byte record type, and a
big-endian payload length. Keep the complete record at or below the Gateway's
64-KiB WebSocket frame limit. Required record types:

| Direction | Record | Payload |
| --- | --- | --- |
| client -> endpoint | `OPEN` | bounded JSON: columns, rows, requested shell (`default` only initially) |
| client -> endpoint | `STDIN` | opaque terminal bytes |
| client -> endpoint | `RESIZE` | bounded columns/rows |
| client -> endpoint | `CLOSE` | empty or bounded reason code |
| endpoint -> client | `OPENED` | bounded JSON with terminal/session metadata, no host details |
| endpoint -> client | `STDOUT` | opaque PTY bytes (PTY combines stdout/stderr) |
| endpoint -> client | `EXIT` | bounded exit code/reason |
| endpoint -> client | `ERROR` | stable public error code, no broker/container details |

Require `OPEN` first and allow it only once. Reject unknown types, invalid
version, oversized/incomplete payloads, illegal dimensions, input after close,
and excess buffered data. Set deadlines for OPEN and broker connection. Apply
backpressure to both directions with small bounded buffers; do not create an
unbounded queue. Do not send heartbeat records that defeat Gateway idle expiry.

The browser terminal will encode keystrokes as `STDIN`, decode `STDOUT` into
xterm.js, and send `RESIZE`. The existing WSS authentication message and Gateway
`ready` JSON happen before this binary subprotocol begins.

### 5.2 Narrow broker contract

Access_Container connects only to its mounted Unix socket. The socket is bound to
one runtime on the Worker side; neither browser records nor access-service
configuration may select a container ID, image, host path, Docker command, user,
mount, capability, or arbitrary exec command.

The internal broker handshake must authenticate a random runtime-specific token
from a mounted file using constant-time comparison. After authentication the
allowed operations are exactly:

- open the configured default shell with a PTY in the pre-bound container;
- write PTY input;
- resize that PTY within configured bounds;
- receive PTY output and exit status;
- close/cancel the shell.

The future broker will map this to Docker Engine exec create/start/resize. It
must set `Tty=true`, `AttachStdin/Stdout/Stderr=true`, `Privileged=false`, an
operator-selected `User`, and the verified image `WorkingDir`. It must select the
container from its server-side runtime record, not client input.

For this phase, implement a fake broker in tests that starts a local PTY child.
That proves framing, resize, cancellation, and lifecycle without granting the
access image Docker control. Document the contract so the later Worker agent can
replace the fake without changing Access_Container.

### 5.3 Access lifecycle and observability

- Bound concurrent sessions per access endpoint (default one) and reject excess
  connections before opening a broker PTY.
- On EOF, timeout, invalid record, broker failure, or SIGTERM, cancel both relay
  tasks, close the PTY through the broker, close transports, and release capacity.
- Never reconnect a dropped byte stream to an existing shell automatically. A
  future explicit resume protocol may do that with a new grant.
- Log runtime/resource ID, opaque session ID, outcome, duration, and byte counts.
  Never log tickets, enrollment keys, broker tokens, terminal bytes, commands,
  environment variables, container inspection, or submitted file names/content.
- Readiness failure must not expose a listener as ready to management probes.

## 6. Interactive image data model

Do not overload the batch `Job` state machine. Add separate Scheduler models,
schemas, routes, and services, for example:

```text
Scheduler/app/models/interactive_workspace_model.py
Scheduler/app/schemas/interactive_workspace_schema.py
Scheduler/app/services/interactive_workspace_service.py
Scheduler/app/api/interactive_workspace_route.py
```

Use a stable workspace plus immutable revisions:

### `InteractiveWorkspace`

- `id`: opaque UUID
- `owner_user_id`: immutable foreign key and indexed
- `name`
- `source_type`: `UPLOAD` or `EXISTING_JOB`
- `source_job_id`: nullable; immutable when present
- `current_revision_id`: nullable until a revision is ready
- `created_at`, `updated_at`

### `InteractiveImageRevision`

- `id`: opaque UUID
- `workspace_id` and monotonic `revision_number`, unique together
- `origin`: `UPLOAD`, `EXISTING_JOB`, and reserved future `SNAPSHOT`
- `source_object_key`: for an upload; never returned to another user
- `source_image_tag`: for an existing job; internal only
- `requested_base_image`: allowlisted UI value for a new upload
- `resolved_base_digest`: filled by the builder
- `state`: `QUEUED`, `BUILDING`, `IMAGE_READY`, `FAILED`, `CANCELLED`
- `image_tag` and canonical `image_digest_ref` (`repo@sha256:...`)
- `failure_type`, redacted `failure_reason`
- builder lease fields equivalent to the batch build fencing fields:
  `builder_id`, unique `attempt_id`, `started_at`, excluded builder/until
- `created_at`, `updated_at`

Use database constraints for owner/source consistency, revision uniqueness, and
required image fields in `IMAGE_READY`. Add explicit additive PostgreSQL
migrations and model-based test schema creation, following current repository
practice. Do not silently depend on `create_all` to alter production tables.

State transitions:

```text
QUEUED -> BUILDING -> IMAGE_READY
   ^         |-----> FAILED
   |         |-----> QUEUED       (expired/released system attempt)
   `---------------- CANCELLED     (owner cancellation before ready)
```

All builder callbacks require both builder ID and attempt ID. A stale attempt may
not publish readiness/failure for a replacement attempt. `IMAGE_READY` is
terminal for an immutable revision. A later save/fork creates another revision;
it never mutates an existing ready revision.

## 7. User API and authorization

Add a user-authenticated router at `/interactive/workspaces`. Prefer separate
strict endpoints instead of one ambiguous multipart body:

- `POST /interactive/workspaces/from-upload`: multipart name, selected allowlisted
  base-image identifier, and ZIP. Reuse safe archive/object-store validation; do
  not accept a raw Dockerfile or arbitrary `FROM` value in this phase.
- `POST /interactive/workspaces/from-job`: JSON name and `source_job_id`.
- `GET /interactive/workspaces`: only the current user's workspaces.
- `GET /interactive/workspaces/{id}`: owner only, including current revision and
  safe build status.
- `GET /interactive/workspaces/{id}/build-logs`: owner only; reuse the existing
  object-store/log redaction behavior.
- `DELETE /interactive/workspaces/{id}`: cancel only a queued/building revision in
  this phase; physical image deletion and live-runtime teardown are later work.

For `from-job`, lock/read the source job and require:

- `job.user_id == current_user.user_id`;
- `job.image_tag` exists and represents a completed image build;
- its state is one in which an image is known to exist; training success is not
  required, because a failed training run can still be useful to debug;
- the source reference is copied internally and never accepted from the request.

Return `404` for another user's IDs to avoid existence disclosure. Make creation
idempotent with a client request ID or idempotency key scoped to the user and
exact request hash, so a retried upload does not create duplicate builds.

Add service-authenticated builder endpoints under a clearly internal prefix:

- claim next interactive revision;
- heartbeat a list of active interactive attempts and receive cancellations;
- mark interactive revision ready with tag, digest, and resolved base digest;
- report terminal user-build failure;
- release a system failure back to `QUEUED`.

Do not reuse the batch endpoint that transitions to VRAM estimation. If the
existing builder API has no service authentication, add a separate high-entropy
builder credential via a protected secret file for all new internal endpoints;
never expose claim/callback routes as user routes.

## 8. Docker image-builder changes

Keep one builder process if desired, but implement interactive claims as a
discriminated work item and preserve fairness so one queue cannot starve the
other. Share cancellation, heartbeat, build-log, and push helpers without sharing
the terminal state callback.

### 8.1 New upload build

1. Claim and persist an attempt ID before downloading anything.
2. Download and safely extract the recorded object key. Reject path traversal,
   symlink escape, excessive expanded size/file count, and missing required
   project files. Keep the current `requirements.txt` requirement unless the UI
   and validation contract are deliberately changed together.
3. Resolve the allowlisted base tag to a registry digest and record it.
4. Generate the workload Dockerfile only:
   - `FROM <resolved-reference>`;
   - `WORKDIR /workspace`;
   - copy submitted files;
   - install requirements;
   - add non-secret OCI/DML labels for workspace/revision/source provenance.
5. Do not install Tailscale, OpenSSH, terminal agents, Docker tools, sudo, access
   keys, or runtime secrets. Do not add a user command that starts training.
   The later Worker runtime overrides the command with its keepalive.
6. Build under the current cancellation fence, push an attempt/revision-specific
   tag, then resolve and return the canonical repository digest.
7. Mark only the claimed revision `IMAGE_READY`; remove local build images after
   the successful callback, following current cleanup semantics.

### 8.2 Existing-job build

The Scheduler supplies only the source job's server-owned `image_tag`. The
builder pulls it, resolves its content digest, and builds a tiny derived image:

```dockerfile
FROM <source-repository>@sha256:<resolved-digest>
LABEL ...workspace/revision/source-job provenance...
```

Push this under the interactive revision's own attempt-specific tag and report
its digest. The extra image reuses layers and provides an independently versioned
interactive artifact while satisfying the requirement that every interactive
workspace has a pushed image. Never trust a source image reference sent by the
browser.

Classify invalid archives, dependency/build failures, and a permanently missing
source job image as user/actionable failures. Classify Docker daemon, network,
registry authentication, and transient registry failures as system failures for
bounded retry. Ensure failed/stale attempts cannot leave Scheduler pointing at a
tag they pushed after losing the lease.

Record both human-readable tag and immutable digest. The later Worker must use
the digest. Docker Hub credentials remain only in the builder; do not copy them
into either output image.

## 9. User UI changes

Refactor `UI/User/src/pages/SubmitJob.tsx` or introduce a dedicated interactive
creation page with an explicit first choice:

- `Batch job` retains the existing form and behavior unchanged.
- `Interactive workspace` reveals a second choice:
  - `Upload a new workspace`: name, PyTorch/CUDA base selection, ZIP;
  - `Use an existing job`: name and an owner-filtered job dropdown containing
    only jobs with an available image.

Interactive creation must not ask for run command, resume command, priority,
VRAM estimate, or scheduling options. Use a discriminated TypeScript request
type so the UI cannot accidentally send fields from the other mode.

Add an interactive workspace details page showing source, revision, image state,
safe build logs, tag/digest abbreviation, and failure reason. In this phase,
display `Image ready` after a successful push. Do not show an enabled Connect or
Save Image action until runtime orchestration exists; a disabled control must say
that runtime placement is not yet available rather than implying the image is
running.

Remove the current service-layer fallback that silently substitutes mock data for
real API failures on these new screens. Authentication/404/build errors must be
visible and must never show another user's cached data.

## 10. Future save/commit contract (design now, implement with Worker runtime)

The user requirement to save changes is valid, but it belongs to the later
runtime phase. Reserve `SNAPSHOT` as a revision origin and use an explicit `Save
as new revision` action, never overwrite a ready tag.

The future sequence is:

1. Scheduler verifies owner, active runtime generation, and no save already in
   progress; it returns an idempotent save operation/fencing token.
2. Worker stops new terminal sessions and drains/closes the current PTY.
3. Worker pauses the **workload container only** and commits it to a new local
   revision. It never commits Access_Container or the Tailscale sidecar.
4. A trusted Worker/builder path pushes the new revision and reports the
   canonical digest under the save fencing token. Registry credentials never
   enter the workload or access containers.
5. Scheduler atomically marks the new immutable revision ready, then may restart
   a runtime from that digest.

Do not mount `/workspace` as a volume if snapshot behavior relies on Docker
commit: Docker explicitly excludes mounted-volume data from commits. If a later
design requires durable volumes, snapshot the workspace separately and rebuild
the image; test that path instead of assuming commit includes it. Also ensure the
workload never receives credentials or sensitive injected environment values,
because committed container configuration/files may preserve them.

## 11. Test plan

### 11.1 Access_Container unit tests

- Parser accepts headers/payloads split one byte at a time and multiple records
  coalesced into one read.
- Reject wrong version, unknown record type, oversized length, excessive buffered
  partial record, duplicate/non-first OPEN, malformed resize, and STDIN after
  close without leaking payloads into logs.
- OPEN and broker deadlines expire deterministically with an injected clock.
- Capacity races allow only the configured session count and release exactly
  once on every error/cancel path.
- Backpressure tests prove producers cannot grow an unbounded in-memory queue.
- Configuration rejects missing/default secrets, non-loopback listen addresses
  in production mode, unsafe socket paths, and overly permissive token files.
- Readiness distinguishes missing broker, failed authentication, and live broker
  while returning only a generic public error.

### 11.2 Access component tests with fake broker

- OPEN creates a real local PTY through the fake Unix-socket broker; `pwd` and an
  environment probe return the fake workload values, proving output is not from
  Access_Container.
- Interactive echo, control characters, UTF-8, large output, and terminal resize
  work across randomly fragmented TCP reads.
- Client disconnect, endpoint shutdown, broker crash, and child exit close the
  counterpart and reap the PTY process with no leaked session/capacity.
- A bad runtime token, wrong socket, replayed handshake, and attempted arbitrary
  container/command fields are denied.
- Image inspection asserts no Docker socket mount, Docker CLI/SDK, SSH server,
  Tailscale binary, registry credentials, or user project files exist in the
  access image.

### 11.3 Scheduler/API tests

- New upload creates one workspace/revision and object key owned by the active
  user; an idempotent retry returns the same resource.
- Existing-job creation succeeds for the owner's image-bearing job and returns
  404 for another user's job, absent job, or non-image-bearing job.
- List/detail/log endpoints never return other users' workspaces or internal
  object keys/source tags.
- Interactive claims never appear in batch `unbuilt_jobs`/`pull_job` responses
  and never transition to VRAM or runnable states.
- Two builders cannot claim the same revision; stale attempt callbacks and late
  pushes are rejected; heartbeat expiry requeues only the still-current attempt.
- Ready callback requires a valid canonical digest and atomically sets the
  workspace's current revision.
- Cancellation, user-build failure, system retry, duplicate callback, and
  database rollback paths have explicit state assertions.
- Internal builder routes reject missing/wrong service credentials and unknown
  fields.

### 11.4 Image-builder unit/integration tests

- Generated upload Dockerfile contains only workload files/dependencies/labels
  and a digest-resolved base; it contains no access packages or secrets.
- Existing-job builds use the server-provided source reference, resolve it to a
  digest, preserve its filesystem, add only provenance metadata, and push a new
  revision tag.
- Tags are safe, attempt-specific, and cannot collide across retries/revisions.
- Push success records the digest; an auth/network error retries as bounded
  system failure; stale cancellation before/during build or push never reports
  ready.
- Archive tests cover zip-slip, symlink escape, decompression bombs/file-count
  limit, malformed ZIP, and missing requirements.
- Optional credential-gated Docker Hub E2E builds both source types, pulls by the
  recorded digest, verifies submitted files/imports, and confirms the output
  image lacks Tailscale/SSH/access artifacts. Keep it separate from required
  credential-free CI, matching the current registry E2E convention.

### 11.5 UI tests

- Switching Batch/Interactive and Upload/Existing Job shows only valid fields and
  preserves existing batch submission behavior.
- Upload mode submits the correct multipart request; existing-job mode sends only
  the selected owned job ID and name.
- Empty job list, loading, API failure, forbidden/not-found, building, ready,
  failed, and retry states render correctly.
- Double submit is prevented and idempotency key/request ID is stable across a
  client retry.
- Interactive ready state does not claim a runtime is connected and does not
  enable Connect/Save prematurely.

### 11.6 Portable network E2E

Extend `test/interactive_e2e/` with an Access_Container plus fake broker in place
of the current echo endpoint. Reuse the pinned Headscale/Tailscale stack and prove:

- controller enrolls/registers service `terminal` on 9000; Gateway probe makes it
  ready; owner A receives a grant and completes a fragmented OPEN/input/output/
  resize/exit exchange through real WSS -> SOCKS -> tailnet TCP;
- user B cannot get A's grant, a replayed ticket fails, wrong resource/service is
  denied, and the endpoint cannot reach another endpoint;
- grant/resource revocation, lease loss, endpoint offline, access restart, and
  gateway shutdown terminate the stream and reap the fake PTY;
- logs/artifacts remain sanitized and contain no tickets, keys, broker tokens, or
  terminal content.

Do not claim this portable fake-broker test proves Docker exec, GPU access, or
production multi-host NAT. Those are acceptance gates for the later Worker phase.

## 12. Implementation order and gates

1. **Contracts first**: add `docs/terminal-stream-v1.md`, broker contract, threat
   model, fixed limits, and state/API schemas. Gate: review confirms no browser
   or access input can name a container or arbitrary command.
2. **Access service**: implement parser, broker client, lifecycle, health, image,
   unit tests, and fake-broker component tests. Gate: no Docker socket/tooling and
   all cleanup/backpressure tests pass.
3. **Interactive data model/API**: add workspace/revision migrations, ownership,
   creation/list/detail/log routes, internal builder claims/callbacks, fencing, and
   watchdog behavior. Gate: interactive records cannot enter batch scheduling.
4. **Builder**: implement both build sources, digest resolution, push/callback,
   cancellation, cleanup, and tests. Gate: pulling the returned digest yields the
   expected workload with no access dependencies.
5. **UI**: add creation modes and image-status page with tests. Gate: an owner can
   select either source, reach `IMAGE_READY`, and sees no false Connect promise.
6. **Portable network E2E/CI**: replace the echo payload in an added scenario with
   terminal protocol plus fake broker. Gate: ownership, ticket replay, revocation,
   stream cleanup, and log redaction pass through real Headscale/Tailscale.
7. **Documentation/operations**: document image retention, orphan-tag cleanup by
   exact recorded tags/digests, secret placement, endpoint decommissioning, and
   the later Worker handoff. Never add broad `docker image prune`, volume prune,
   wildcard registry deletion, or Headscale reset behavior.

## 13. Definition of done for this phase

- Access_Container is a generic, pinned, non-root image with a tested framed PTY
  endpoint and narrow authenticated fake-broker integration.
- No access/Tailscale/SSH/Docker dependency or credential is present in a user
  workload image.
- An authenticated user can create an interactive workspace from a new upload or
  one of their existing jobs.
- Each successful request produces an independently versioned Docker Hub image,
  and Scheduler stores both its tag and immutable digest without moving it into
  the batch scheduling state machine.
- Ownership, build fencing, retries, failure reporting, logs, and UI states have
  automated coverage.
- The real Gateway/Headscale portable E2E carries terminal protocol traffic to an
  access endpoint and enforces cross-user/replay/revocation isolation.
- Documentation says clearly that live workload execution, worker placement,
  Docker exec, and saving a modified runtime are the next phase rather than
  pretending a shell in Access_Container is a shell in the user's image.

## 14. Primary technical references

- Docker Engine exec supports PTY creation, attach, user/workdir selection, and
  resize operations: <https://docs.docker.com/reference/api/engine/>
- Docker commits do not include mounted-volume data:
  <https://docs.docker.com/reference/cli/docker/container/commit/>
- Docker volumes live outside a container's writable layer:
  <https://docs.docker.com/engine/storage/volumes/>
- Keep Docker's default seccomp policy and least-privilege capability posture:
  <https://docs.docker.com/engine/security/seccomp/>
- Tailscale userspace mode and the Docker `TS_USERSPACE` behavior:
  <https://tailscale.com/docs/concepts/userspace-networking>
