# Browser workspace editor, durable revisions, and batch-training handoff

Status: implementation plan for the next agent. Writing this document does not
implement the editor, file APIs, snapshots, or training handoff.

## 1. Requested outcome and decisions

Extend the current interactive runtime into a browser code workspace. The owner
starts a ready revision, verifies the workload connection, clicks **Open Editor**,
creates/edits files, and runs commands in an integrated web terminal. They can
then choose **Save for Later** or **Submit for Training**.

Use these button names and semantics:

| Action | Result |
| --- | --- |
| Connect | Keep the existing short workload connection check and its `Connected successfully` message. |
| Open Editor | Open the browser workspace for this runtime/generation; establish a fresh authenticated workspace connection. |
| Save File / Save All | Write editor buffers into the running workload. This is temporary until a revision is saved. |
| Save for Later | Flush dirty buffers, capture the workload filesystem, publish an immutable revision, and keep the runtime running. |
| Submit for Training | Collect training settings, flush buffers, capture/publish the current changes, stop/release the interactive runtime, and create a normal batch job from that exact revision. |
| Stop Workspace | Use the existing stop/revocation/cleanup path, with clear handling of unsaved buffers and unfinished saves. |
| Reconnect / New Terminal | Obtain fresh authorization or start a new default-shell PTY; never replay a consumed ticket. |

The existing connection is authenticated WSS through Gateway/Access to a Docker
exec PTY. The user's reference to SSH describes the remote shell experience;
this phase must build on that actual transport. Do not add an SSH daemon,
browser-to-Worker public ports, or native SSH key management.

The new work explicitly extends the earlier phase boundaries that kept editing
and Save disabled. Preserve their scheduling, authentication, isolation, leases,
generation fences, exact cleanup, and image immutability.

The implementation must deliver real files, real terminal I/O, durable saved
images, and executable batch jobs. A mocked file tree, browser-only persistence,
terminal simulation, or button that only changes a job status is insufficient.

## 2. Read the existing implementation before changing it

Read `plan.md`, `plan2.md`, `changes2.md`, `changes3.md`, and the operations/access
documents. Check the actual code rather than assuming every runtime-host gate
was run. `changes3.md` separates passing local checks from pending real
Docker/GPU/registry/multi-host acceptance. The editor does not remove those gates.

| Area | Files/contracts to inspect |
| --- | --- |
| Workspace/revision source | `Scheduler/app/models/interactive_workspace_model.py`, `app/services/interactive_workspace_service.py`, `app/api/interactive_workspace_route.py`, `migrations/001_interactive_workspaces.sql` |
| Runtime ownership/holds | `Scheduler/app/models/interactive_runtime_model.py`, `app/services/interactive_runtime_service.py`, `app/services/scheduling/`, `migrations/002_interactive_runtimes.sql` |
| Controller/readiness/grants | `Scheduler/app/services/interactive_controller.py`, `interactive_management_client.py`, `app/api/interactive_runtime_route.py` |
| Worker protocol/journal | `Scheduler/app/api/worker_execution_route.py`, `worker_execution_route_auth.py`, `app/schemas/worker_execution_schema.py`, `Worker/managed_worker.py`, `execution_state.py`, `scheduler_protocol.py` |
| Real workload/PTY/cleanup | `Worker/interactive/{docker_ops,manager,broker,endpoint,cleanup}.py`, `Worker/executor.py`, `hardware.py`, `runtime_config.py` |
| Endpoint transport | `Access_Container/interactive_access/{main,session,broker_client,protocol,config}.py`, `Gateway/interactive_gateway/`, `Headscale_Management/headscale_management/{schemas,endpoint_service,grant_service,enrollment_service}.py` |
| Image publishing | `Docker_Image_Builder/{builder,docker_ops,api,interactive_build,interactive_api}.py`, builder journals and tests |
| Batch contracts | `Scheduler/app/models/job_model.py`, `app/services/job_service.py`, `app/api/jobs_route.py`, `app/schemas/job_schema.py`, `Worker/executor.py`, `Worker/object_store.py` |
| Object storage | `Object_store/main.py`, `init_buckets.py`, deployment/private-network settings; existing generic upload/presign/read routes are not snapshot authorization |
| Frontend | `UI/User/src/App.tsx`, `pages/{InteractiveDetails,InteractiveWorkspaces,SubmitJob,JobDetails}.tsx`, `services/{interactive,terminalVerification,jobs,auth}.ts`, shared styles/layout |
| Verification/deployment | Existing Scheduler/Worker/Builder/Access/Gateway/Management/UI tests, `Scheduler/test/postgres/`, `test/interactive_e2e/`, `.github/workflows/ci.yml`, `docs/interactive-runtime-operations.md`, `deploy/interactive/worker/` |

Important current behaviors to account for:

- `InteractiveDetails` verifies a connection, sends CLOSE, and closes its socket.
  The editor needs a new, persistent connection with a new single-use grant.
- `terminal-stream-v1` is terminal-only. Do not silently reinterpret its CLOSE,
  EOF, metadata, framing, or state machine to mean workspace/file operations.
- Management registers one immutable named service/version per resource and
  binds an enrollment's identity to that resource ID. Do not invent a second
  resource using the same enrollment without changing that binding contract.
- `SNAPSHOT` is present in the revision origin constraint, but capture and
  publishing are not implemented. Add the actual Builder path and constraints.
- The active runtime pins its revision/digest. Saving a newer revision must not
  mutate that runtime's pinned source or the old IMAGE_READY revision.
- Current workspace public output chooses the latest revision, and publication
  changes `current_revision_id`. Separate latest/publishing revision from the
  latest successful saved revision; a failed or late older publication must not
  hide or replace a newer successful saved source.
- Batch `handle_training()` normally executes image CMD. Merely setting
  `Job.command` does not make that command run on first training placement.
- Batch output mounting seeds image WORKDIR into a host job directory. Ensure
  this copies the saved edited contents; an empty mount must not hide them.
- `Job.object_key` is currently required and unique. Reusing one snapshot object
  key for several jobs conflicts with that schema; do not insert dummy ZIPs.
- Worker registry credentials are pull-only. Publishing must remain on the
  trusted Builder, without giving registry push credentials to Worker's workload.

## 3. Product flow and workspace UI

### 3.1 Entry and routing

On the details page retain Start/status/Stop/Connect. Show **Open Editor** after
the current runtime/generation passes Connect. Disable it when that runtime is
not READY/RUNNING or lacks editor capability. Clear the verified state after
generation changes or health/authorization failure.

Use an authenticated route such as `/interactive/:id/editor`. Passing a recent
verification result through navigation is a UX hint, not authorization. A direct
URL or reload must load owner-visible workspace/runtime state and perform fresh
authorization and application-handshake validation. Never put a ticket in the
route, query, router history, or local/session storage.

For an older terminal-only runtime show a useful upgrade message and retain
terminal verification. Do not reconnect it under a different service identity.

### 3.2 Editor layout

Provide a recognizable, responsive code-editor layout:

- Header: workspace name, source/saved revision, runtime status, connection
  status, **Save All**, **Save for Later**, **Submit for Training**, **Stop Workspace**.
- Left explorer: lazy directory expansion, Refresh, New File, New Folder,
  Rename, and confirmed Delete. Show empty directories and loading/error states.
- Center: file tabs, syntax-highlighted text editor, dirty marker, line numbers,
  search, undo/redo, sensible keyboard shortcuts, and conflict/error messages.
- Bottom panel: real interactive terminal, resize support, terminal connection
  state, and **New Terminal** after shell exit. One terminal is sufficient.
- Status/footer: file path, language/encoding, live-file save status, latest
  durable revision/time, and capture/publishing/training progress.

Use Monaco Editor for the text editor and xterm.js with its fit addon for the
terminal. Select compatible maintained releases, pin them in the lockfile,
bundle assets/workers locally through Vite, and document CSP/worker settings.
Do not depend on a hosted IDE iframe or runtime CDN scripts. Preserve keyboard
accessibility, focus, readable contrast, and the existing app's visual style.

Large/binary/invalid UTF-8 files get a clear read-only/unsupported message;
do not corrupt them by converting to text. Initially cap editable text files at
2 MiB. No language server, extension marketplace, debugger, collaborative editing,
HTTP app preview, port forwarding, or multiple terminals is required.

### 3.3 Save and submission UX

**Save File** means written to the running container. Label this distinction:
`Written to workspace · save a revision to keep it after Stop`.

**Save for Later** first flushes every dirty buffer and stops on any conflict.
Explain that terminal sessions close briefly during capture, disk state is saved,
and running processes/sessions are not saved. Show capture/upload/publication
progress. Report success only after the immutable image is published and its
revision is durable. The editor can resume after capture/upload is durable;
edits made afterward are not included in that snapshot.

Show `Revision N saved at <time>`, with a revisions list and **Start from This
Revision**. A later runtime must actually load that revision's digest, including
file changes and installed dependencies. A browser reload must recover operation
status through Scheduler and not start another capture automatically.

**Submit for Training** opens a dialog with job name, required training command,
optional resume command, and the normal batch settings already supported by the
application. Show the exact revision/workflow being submitted and explain that
the interactive session will end after a successful save. Default priority is
normal; use existing priority-request rules.

Use a training command compatible with the existing Python VRAM estimator, for
example `python train.py --epochs 10`. Validate the supported Python invocation
grammar server-side. Unsupported shell command chains should get an actionable
error, rather than being submitted and failing estimation later. The interactive
terminal remains a normal shell for arbitrary commands within the workload.

Submission captures the current state even if a previous revision was saved.
Do not submit an old revision while presenting unsaved current edits as included.
While submission is active, make editing/terminal input read-only and expose
durable progress: Saving -> Publishing -> Stopping workspace -> Waiting for
cleanup -> Preparing job -> Queued for estimation. Once the batch Job exists,
provide **View Training Job** and the existing job detail/log/output experience.

On capture/publication failure, retain dirty buffers and the running workspace
where its lease allows. Do not stop it or create a runnable job. If cleanup or
management revocation stalls after publication, retain the saved revision and
show `Waiting for workspace cleanup`; never force-clear the machine hold.

Warn on route exit/reload/Stop when there are dirty buffers or live changes not
checkpointed. A file-save acknowledgement is not durable snapshot success.
Do not promise that background terminal processes' writes are continuously
tracked by an editor dirty marker. Use snapshot time and conservative change
indicators instead of claiming the entire filesystem is permanently clean.

## 4. Keep the existing transport and add an explicit workspace protocol

Keep this architecture:

```text
Browser -- HTTPS --> Scheduler: ownership, runtime state, grants, saves, submission
Browser -- WSS --> Gateway blind byte relay
                --> private tailnet endpoint TCP 9000
                --> per-runtime Access
                --> authenticated runtime Unix broker
                --> exact workload: default-shell PTY + bounded file operations

Worker -- authenticated outbound HTTPS --> Scheduler: fences, operations, receipts
Worker -- scoped outbound HTTPS --> private snapshot artifact storage
Builder -- scoped artifact read + registry push --> immutable image publication
```

No Docker socket, controller secret, registry credential, upload capability,
host filesystem, or Headscale administrative secret enters the workload/Access
or browser. Keep workload networking and mounts isolated as in plan2. The
terminal does not grant network access beyond the workload's configured policy;
do not enable public inbound ports or silently relax `network_mode=none`.

Add a documented application protocol **workspace-stream-v1**, distinct from
terminal-stream-v1, carried inside the existing `tcp-stream-v1` transport. Use
the bounded six-byte record framing, explicit message types, and a separate
parser/state machine. Keep old terminal clients/probes and tests working.

For new editor-capable runtimes pin `access_service=workspace` and application
capabilities/version at Start. Register the existing single resource/enrollment
as this service on the existing allowlisted port. Pin terminal-only behavior for
old runtimes. Extend the grant URL/service selection and controller registration
to read this immutable runtime field instead of hardcoding `terminal`.

Do not mutate a live endpoint's service to upgrade it. Capability upgrades take
effect on a new runtime/generation. Management's transport protocol remains
`tcp-stream-v1`; its existing service/owner/version/grant checks remain enforced.

The editor-capable endpoint can retain the legacy terminal-only OPEN path for
the short Connect verification. Workspace clients start with a distinct HELLO,
then receive application READY/capabilities before requesting files or a PTY.
Dispatch these modes explicitly, with no heuristic based on arbitrary user data.

Define and document exact records before implementation:

| Family | Required behavior |
| --- | --- |
| HELLO / WORKSPACE_READY | Version/capabilities, server-bound runtime/generation, project root display name, limits; validated before enabling controls. |
| File request/result | Request ID, operation, relative paths, preconditions, bounded metadata, typed safe outcomes. |
| File chunks/end | Request ID, ordered sequence, bounded bytes, total size/hash; exact completion and cancellation semantics. |
| PTY OPEN/OPENED/STDIN/STDOUT/RESIZE/CLOSE/EXIT | Existing default-shell semantics in an explicit workspace mode; at most one active PTY. |
| WORKSPACE_STATE / CLOSE | Capture/read-only/unavailable states and closing the workspace connection. |
| ERROR / CANCEL | Bounded typed errors and exact request cancellation; no host/SDK traceback. |

In workspace mode, PTY CLOSE/EXIT ends only that PTY and keeps file access alive.
New Terminal can open another PTY after cleanup is proven. Closing the workspace
socket closes its PTY/file requests without stopping the runtime. Legacy
terminal-stream-v1 still retains its whole-connection CLOSE/EOF semantics.

Use one workspace WSS session with bounded multiplexing for files and terminal;
it fits the current one-session default without racing separate terminal/file
grants. Reject duplicate request IDs, unknown fields/types, invalid state
transitions, booleans used as integer dimensions, partial EOF, and excess data.
Choose different explicit caps for metadata and content; the old 1,024-byte JSON
metadata limit must not be used to transport whole files.

Initial limits: 65,536-byte transport frames/records, 16 KiB metadata, 2 MiB text
file, 32 KiB content chunks, 4 concurrent file reads, 1 file mutation, bounded
directory pages of 200 entries, and at most 1 MiB total unsent data per connection.
Implement awaited backpressure, terminal-output fairness, request deadlines,
abort handling, and buffer release. Slow file reads must not starve terminal
input or build an unbounded queue. Split large browser writes into framed chunks.

Gateway remains a byte relay; no remote Docker/file API is added there. Access
validates the workspace application protocol and proxies only to its bound
broker. Worker derives container ID/User/root/generation exclusively from its
current server-side assignment, never from browser-selected identifiers.

Keep existing Origin checks, active-user/owner checks, single-use grants,
authorization renewal, absolute session deadlines, health probes, and revocation.
Reconnect uses a fresh grant and starts a fresh application session; terminal
process survival or stream replay is not guaranteed. Preserve dirty buffers in
memory across a transient disconnect and require conflict checks before writing.

## 5. Implement real, bounded file operations inside the workload

Add Worker-side modules such as `Worker/interactive/workspace_protocol.py` and
`file_service.py`, plus a small fixed file helper executed inside the exact
workload container. Match Access/browser protocol modules to the written spec.

Use Docker exec without a PTY for the helper, as the workload's verified User,
with a fixed helper program, fixed interpreter/arguments, and bounded stdin/stdout.
Never turn a browser path into a shell command or run file operations on the
Worker's host path. Do not mount the workload filesystem into Access.

The initial implementation may require a working Python 3 interpreter for the
helper, consistent with the ML image/estimator requirements. Probe compatibility
before advertising editor capability. Unsupported images must have a clear
error, not a fake file pane or an unrestricted fallback. The helper receives no
credentials and its output remains untrusted and bounded at the host broker.
Avoid baking temporary helper/session files into saved revisions unnecessarily.

The explorer root is the validated image WORKDIR, normally `/workspace`. Pin it
at launch. Refuse an editor root of `/`, a symlink root, or a missing/unreadable
project root; do not create privileged directories or change workload ownership
just to make an incompatible image appear usable. The terminal keeps its existing
User/WORKDIR and may access other locations allowed by that User.

Required operations: paginated list, stat, read text, create file/folder, write
text, rename, and delete file/empty folder. Recursive deletion, arbitrary archive
extraction, and arbitrary host paths are outside this initial feature.

Path and resource rules:

- Paths are relative to the server-pinned root. Reject absolute paths, `..`, NUL,
  backslash ambiguity, excess length/depth, and invalid encodings. Return opaque
  safe errors for unavailable paths; never expose Worker host paths.
- Resolve using directory descriptors/no-follow operations and revalidate on
  the actual syscall. A string `realpath` prefix test followed by an ordinary
  `open()` is insufficient under symlink/rename races.
- Show symlinks as symlinks; initially refuse editing/traversing them. Reject
  FIFOs, devices, sockets, and unsupported file types without blocking.
- Restrict writes to writable regular project files, reject multiply-linked
  files for mutation, and refuse unsafe rename/delete targets. Recheck identity
  before replacing a target so a terminal-created link cannot redirect a write.
- Preserve ordinary permissions where appropriate; new files use a safe umask.
  Browser metadata cannot request root, ownership changes, special mode bits,
  or privileged execution.
- Enforce total/stream limits before buffering or writing. Clean incomplete
  temporary writes and leave the original file intact on errors/cancellation.

Reads return a content fingerprint/version and file identity. Editor writes
carry the expected version; mismatches return a conflict without silently
overwriting the known changed file. Serialize cooperating editor mutations and
use atomic temporary-file replacement. Rename/delete also need identity/version
preconditions. Conflicts offer Reload or an explicit user overwrite flow after
fetching the latest version; keep the unsaved buffer available for comparison.

A content-hash check and rename cannot guarantee atomic compare-and-swap against
arbitrary non-cooperating terminal/background writers. State that limitation,
detect known changes, and advise against editing the same file simultaneously
from a terminal. Do not claim filesystem-wide transactional file editing.

Refresh after terminal commands or provide bounded refresh polling/manual
Refresh. Keep responses fenced to the request's runtime/generation; a late read
from an old runtime cannot populate a new editor. No source, keystrokes, file
contents, or terminal bytes go into control-plane logs or telemetry.

## 6. Durable Save for Later: capture the actual filesystem

### 6.1 What is saved

Save an immutable workload image snapshot, including the image filesystem and
its writable-layer changes: edited/created/deleted files and dependencies already
installed inside that filesystem. This works for both uploaded workspaces and
workspaces derived from an existing job image.

Do not implement Save as a browser cache or only a source ZIP: that would lose
terminal changes outside the project and installed dependencies. Processes,
memory, terminal sessions, environment exports in a shell, and GPU state are not
restored. Editing `requirements.txt` does not itself install packages; do not
silently rerun dependency installation on snapshot publication.

Capture only the workload. Access/sidecar state, broker tokens, credentials,
Docker mounts/devices, runtime resource policy, and network/host metadata must
never become workload image content/config. Existing plan2 volume rejection
remains important: Docker commit does not capture mounted volume contents.

### 6.2 Capture and publication sequence

1. Browser flushes dirty buffers and resolves conflicts. Scheduler creates a
   durable, idempotent save operation bound to the current owner, workspace,
   runtime/generation, assignment/instance/attempt token, and parent revision.
2. Worker learns of the operation through its authenticated execution control
   path while continuing heartbeats. Persist operation ID/capture token and
   progress in the journal before any Docker side effect. A snapshot operation
   is a lifecycle action within the held interactive assignment, not a new batch
   claim or a new unaccounted launch.
3. Gate new file mutations/PTY input/open operations, drain in-flight mutations,
   notify the client, and close/reap the current PTY with the existing pidfd/
   exact-container fallback guarantees. Background workload processes are paused
   during capture; application-specific database consistency is not promised.
4. Revalidate the lease/fence and capture the exact labelled workload via Docker
   commit with pausing enabled. Preserve the immutable original source metadata
   needed for a usable saved image; explicitly remove runtime keepalive
   entrypoint/CMD and runtime-only labels/config. Never commit Access or sidecar.
5. Persist the returned image ID and server-generated operation tag immediately.
   Export a bounded Docker image archive by exact ID, stream it to private
   artifact storage, and record byte size, SHA-256, source image ID/platform,
   original launch metadata, and immutable artifact/version receipt. No registry
   push secret is needed on Worker.
6. Scheduler independently verifies completion/receipt for the authorized
   operation before inserting/enqueuing its SNAPSHOT revision. The Builder must
   not claim a revision before its artifact is complete and verified.
7. Once capture/upload is durable, Save for Later can reopen the live editor/
   terminal. Builder imports the exact verified archive, validates image identity/
   platform/User/WORKDIR/volume/config constraints, tags it under the established
   workspace/revision/attempt namespace, and pushes using Builder-only secrets.
8. Resolve and verify the registry digest. Report through existing fenced
   publication callbacks extended for SNAPSHOT provenance. Mark the immutable
   revision IMAGE_READY and save operation SUCCEEDED atomically. Advance the
   workspace's saved head with a monotonic/CAS rule; do not alter active runtime.
9. Reconcile local temporary tags/images/files and abandoned uploads by exact
   operation identity. Retain durable artifacts/images referenced by revisions
   or jobs. Shared base layers/images and unrelated objects are never pruned.

Use the Docker API configuration/changes capabilities to produce deliberate
saved image metadata, rather than persisting the launched container's keepalive
configuration wholesale. Store the source image's safe metadata at runtime
launch. Preserve its verified User/WORKDIR and necessary workload environment;
remove runtime infrastructure secrets/config and reject unsafe image volumes.
The training image's command is established separately in section 9.

Commit/upload can be slow: run them off the heartbeat/UI event loop, enforce
space/headroom, size, pause/capture/upload deadlines, and cancellation checks.
Avoid buffering whole images in RAM. Heartbeats/authorization/lease guard continue
through capture and upload. Do not temporarily release the interactive machine.

Persist a monotonic/boot-bound capture-pause deadline in the local journal and
have the independent supervisor enforce it even when normal heartbeats keep
renewing the assignment. A hung snapshot thread must not leave a paused workload
indefinitely authorized. On deadline breach, deny further capture/editor effects,
attempt bounded exact unpause/stop/cleanup, and quarantine unresolved daemon
operations. Dropping an HTTP request is not proof Docker commit was cancelled;
reconcile late images by the operation's exact metadata before acknowledging
cleanup. No timeout alone releases the assignment.

A bounded intentional capture pause must not be mistaken for unhealthy workload
execution. Expose a persisted capture phase/read-only capability, keep base
health/probe authorization valid while that phase is within its deadline, and
continue serving availability/state messages. This is not permission to report
healthy indefinitely for a hung commit or failed/expired workload.

All exit paths restore the intended pause/input gate or proceed into exact Stop
cleanup. A late commit/create/upload completion after Stop/lease loss cannot
publish into a different generation or leave untracked local objects.

### 6.3 Artifact storage must be genuinely private

The current Object_store generic endpoints accept caller-selected keys/buckets,
buffer complete uploads, and are not snapshot authorization. Do not send large
image archives through those endpoints or expose snapshot archives/presigned
write permissions to the browser.

Add a protected snapshot-artifact namespace/API (or equivalent private storage
adapter) with streamed/multipart bounded uploads, distinct service credentials,
and short-lived capabilities scoped to one exact operation/upload attempt/key.
Worker receives only its capability through authenticated HTTPS, in memory;
Builder receives only authorized artifact reads for its live publish attempt.

Prevent generic unauthenticated upload/read/list/presign routes from bypassing
the new namespace's authorization. Keep its bucket/storage endpoint private and
document cross-host HTTPS/CA and role configuration. Scheduler/storage must
validate receipt/size/hash; trusting a browser-provided artifact key is forbidden.

Presigned PUT URLs alone do not make a blob immutable or single-use. Use durable
upload-attempt state and immutable object versions/keys with verified completion;
bind the recorded version/checksum, deny subsequent replacement, and validate
again on Builder download. Failed/retried uploads use exact staged objects and
cannot overwrite a revision's accepted artifact. Persist receipt/IDs, not raw
temporary capabilities. Abort/cleanup only the exact abandoned multipart upload.

Validate archive structure, declared config/layer identities, bounds, and
server-owned tag namespace before Docker load. Do not extract image archives
into host paths or allow imported tags to overwrite arbitrary host images.
Use the trusted isolated Builder environment already responsible for untrusted
image construction; do not load these archives on Scheduler/Gateway/Access.

## 7. Persistence, migration, and operation state machines

Add an explicit additive migration after 002, e.g.
`Scheduler/migrations/003_workspace_editor_snapshots.sql`. Keep migrations ordered,
idempotent, advisory-locked, and test an upgrade from the existing schema.

Suggested records:

| Record | Required data/invariants |
| --- | --- |
| Runtime additions | Immutable access service/application version/editor capability and validated root/source image safe metadata; defaults preserve existing terminal-only runtime behavior. |
| Save operation | Owner/workspace, source runtime/generation/assignment/instance/token binding, parent revision/digest, purpose SAVE/TRAIN, request key/hash, state, capture attempt/lease, artifact receipt/hash/size/platform/image ID, target revision, timestamps, typed safe error. |
| Snapshot revision additions | Parent/source runtime provenance, accepted save operation/artifact binding, original safe image metadata, immutable published digest; strengthened SNAPSHOT constraints. |
| Training submission | Owner/workspace/runtime/generation, exact save operation/revision, validated training settings, idempotency key/hash, workflow state, stop/release prerequisite, final batch job ID, timestamps/errors. |
| Batch provenance | Typed source kind ARCHIVE/SNAPSHOT, source workspace/revision/digest, final executable image digest; old jobs keep existing defaults. |
| Artifact/upload attempt | Exact operation/attempt, immutable storage version/receipt/checksum/size, bounded authorization/cleanup state; secrets excluded. |

Save operation transitions:

```text
REQUESTED -> CAPTURING -> UPLOADING -> PUBLISH_QUEUED -> PUBLISHING -> SUCCEEDED
     \---------- controlled failure/cancellation -----------> FAILED/CANCELLED
```

A durable artifact with failed publication must remain recoverable: expose
**Retry Publishing**, reusing that captured content under a new fenced publish
attempt. This can work after the runtime stopped. Do not recapture different
content under the original successful upload's identity or falsely label the
unpublished revision ready.

Training submission transitions:

```text
SAVING -> WAITING_FOR_REVISION -> STOPPING_RUNTIME -> WAITING_FOR_RELEASE
       -> PREPARING_JOB -> JOB_CREATED
       \---------------- typed recoverable/permanent failure -> FAILED
```

Create at most one active save per workspace/runtime, and prevent conflicting
Save/Submit workflows. Same idempotency key/body returns the same operation;
different content/settings with the same key returns 409. Repeated callbacks
with the same accepted hash are harmless; changed results/stale attempt tokens
are rejected. Allocate revision numbers under workspace locking.

Capture attempt state and publish attempt state are separate; reuse existing
Builder leases/fences instead of inventing unfenced publication. Parent revision,
accepted artifact, successful digest, ownership, and released tombstones become
immutable. Composite owner/workspace/runtime/revision relationships and unique
submission-to-job linkage must be database-enforced where applicable.

Use the established Worker-first lock order for transactions involving runtime
assignments. Document a single compatible order for workspace/operation locks;
do not introduce a workspace-then-Worker deadlock with Stop/claims/controllers.
External storage/Docker/registry/management calls run outside database locks,
using persisted attempt leases and conditional reconciliation/compensation.

Do not store a long-lived editor socket as Scheduler's truth. File access requires
a valid authorized live session, and saves/submission require current durable
runtime/fence state. A Scheduler restart or Redis loss cannot duplicate captures,
revive stopped generations, publish stale data, or create duplicate jobs.

## 8. Public and Worker API contracts

Use strict bounded schemas, active-user/owner authorization, safe errors, no-store
grant responses, and `Idempotency-Key` for durable mutations. Public IDs bind to
the authenticated owner; never accept arbitrary Worker/container/registry/key
selection from the browser.

Suggested owner API:

| Operation | Contract |
| --- | --- |
| Existing runtime status | Add safe capabilities/service/application version and active save/submission summaries. |
| POST `/interactive/runtimes/{id}/workspace-connection` | Fresh single-use workspace grant for exact READY/RUNNING generation and capability; late grant after Stop is revoked. |
| Existing `/connection` | Keep short Connect verification, resolving the pinned service correctly. |
| POST `/interactive/runtimes/{id}/saves` | Expected generation/parent revision, purpose SAVE; create/replay durable operation, return 202/status URL. |
| GET `/interactive/saves/{id}` | Owner-visible capture/publication status, saved revision, typed safe failure/retry affordance. |
| POST `/interactive/saves/{id}/retry-publication` | Retry only the accepted artifact under new publication fencing; idempotent. |
| GET `/interactive/workspaces/{id}/revisions` | Bounded paginated history with latest successful head and publishing state. |
| POST `/interactive/runtimes/{id}/training-submissions` | Expected generation and validated job settings; orchestrate fresh capture, stop/release, and batch creation; return 202. |
| GET `/interactive/training-submissions/{id}` | Durable workflow status and final job link; resumes after browser reload. |

Do not route live file content or terminal keystrokes through Scheduler's owner
REST API. They use the runtime-bound workspace stream; Scheduler governs access
and durable workflows.

Extend the authenticated Worker v1 execution API with exact operation discovery/
claim, capture progress, upload capability/receipt, and failure acknowledgements.
Every operation binds the existing Worker identity plus instance/assignment/
attempt/generation and a separate capture attempt token/sequence. Deliver pending
lifecycle actions through heartbeat instructions or an authenticated bounded
operation poll; this must not restart new-work placement polling during a hold.

Persist and replay ambiguous operation delivery and completed upload receipts.
Capabilities may be refreshed for the same authorized upload attempt without
creating a second captured revision. Never accept an owner browser's fabricated
Worker callback or a different Worker's receipt. Errors/limits must be consistent
across schema validation and the raw body middleware.

## 9. Submit an ordinary batch job from the exact saved revision

The default **Submit for Training** sequence is:

1. Validate settings and persist the submission/idempotency record.
2. Capture/publish a new immutable revision through the Save pipeline. Keep
   editing/input gated for this submission so later changes are not misrepresented.
3. After successful publication, request the existing runtime Stop. Revoke
   grants/sessions/resource/enrollment and await exact local cleanup plus Scheduler
   release acknowledgement. Do not perform direct container deletion in a new
   submission route or free the hold on a timeout.
4. After durable release, create exactly one normal batch Job with provenance
   pointing to the captured revision/digest and validated training settings.
5. Let the normal image-builder claim/lease pipeline prepare its executable
   training image, then the normal estimation/training scheduling pipeline run it.
6. Show the job in existing lists/details/logs/output downloads and retain the
   source revision so it can also be started interactively later.

Waiting for release is the chosen product behavior, not a new priority tier.
Preserve estimation -> eligible whole-machine interactive -> retry/training,
non-preemption, durable assignments, and Worker exclusivity. Saved revisions and
submission records are not executable assignments and cannot authorize launch.

For SNAPSHOT-source jobs, implement a small Builder derivation from the exact
saved digest. It must not replace the saved project with the original upload or
rerun requirements installation. Preserve verified WORKDIR/saved filesystem and
produce a deliberate command wrapper, e.g. exec-form CMD invoking the validated
training command with an explicit cleared/compatible ENTRYPOINT. Do not inherit
the runtime keepalive or interpolate untrusted strings into Dockerfile syntax.
Use JSON encoding/validated generation for user command data.

This derivation is necessary because current first training runs use image CMD.
The saved revision itself stays command-independent and immutable; separate
submissions with different commands produce distinct fenced training images.

Add typed source/provenance columns and a snapshot Builder branch. Preserve
`Job.object_key` uniqueness by storing a unique per-job, server-authored source
manifest under a dedicated private namespace. It records the validated snapshot
reference/settings; it is not a fabricated source ZIP. The SNAPSHOT Builder
branch reads/validates the manifest or the equivalent server-fenced claim data
and never feeds it to the ordinary ZIP extractor. Old ARCHIVE jobs retain their
existing upload/requirements behavior. Adapt source-download UI to this distinction.

Final training publication must record a verified immutable digest on Job and
forward it through claim payloads/Worker execution. Keep the attempt tag for
display, but pull/run SNAPSHOT jobs by their final executable digest, never
`latest` or the parent interactive digest by accident. Fence ready callbacks and
all later estimation/results/cleanup using the existing mechanisms.

Normal state progression is `NOT_RUNNABLE -> IMAGE_BUILDING ->
VRAM_ESTIMATION_PENDING -> RUNNABLE -> IN_PROGRESS`, followed by the existing
completion/failure/retry states. Do not skip estimation or reuse previous VRAM
measurements after arbitrary edits. Existing estimator failure behavior applies
when code cannot produce a valid report.

Verify first training, retries with/without resume commands, output baseline
seeding from the edited image, and ownership/provenance. Existing retries should
recover that job's own checkpoints, not another job's or an interactive runtime's
temporary state. Never mutate the original source job when submitting a workspace.

## 10. Stops, failures, crashes, and concurrency

Cover these cases explicitly in implementation and reconciliation:

| Event | Required outcome |
| --- | --- |
| Disconnect/navigation/logout | Cancel file streams, reap PTY, release session; keep workload running unless Stop was requested. No ticket replay or automatic input replay. |
| Stop before/during capture | Gate further effects, cancel capture where possible, handle late exact image result, unpause/stop exact workload safely, and do not release until outstanding work is reconciled. |
| Stop after verified artifact upload | Allow publication of that accepted immutable artifact if the save was accepted before Stop; it no longer depends on a live runtime. Do not treat this as authorization for a fresh capture. |
| Lease loss/Worker instance change | Deny new edits/capture; independent lease guard cleans exact runtime; old instance can reconcile exact cleanup/artifact receipt only under explicit fenced rules. |
| Worker SIGKILL during commit/export/upload | Reconcile durable operation, exact tags/image IDs, upload attempt and pause state on startup. No duplicate capture, zombie pause, or broad cleanup. |
| Lost commit response | Use operation-specific image metadata/tag/journal and Docker reconciliation; ambiguity holds/quarantines rather than committing blindly or releasing early. |
| Lost upload/publish response | Query/replay exact accepted receipt/attempt; never overwrite accepted content or publish a different image under its identity. |
| Registry/storage/management outage | Show recoverable phase/failure; retain accepted artifacts/revisions and reservations where required. No false saved/ready/queued success. |
| Submission reload/double click | Return same workflow; create at most one job. A new deliberate submission needs a new key. |
| Submit/Stop/publication race | Publication prerequisite and exact release checked transactionally; late callbacks cannot revive runtime or attach another revision/job. |
| Multiple tabs/editors | One active workspace session by default; BUSY is explicit. Mutations have version preconditions; no silent overwrite. |
| Background process writes | Paused filesystem snapshot gives a defined capture point; no memory/application-transaction restore claim. |

Track snapshot side effects and outstanding tasks in the journal so cleanup
cannot be acknowledged while a late capture/export can create surviving local
objects. Keep temporary image/artifact cleanup separate from deleting durable
revisions. Provide safe, exact, retryable compensation, including tag/image
ownership and reference checks. No `docker system prune`, global volume removal,
registry overwrite, or Headscale reset.

## 11. Frontend state and terminal details

Create reusable services/components such as:

- `pages/InteractiveEditor.tsx` and workspace/file tabs/explorer/status components.
- `components/WorkspaceTerminal.tsx` using xterm.js and fit addon.
- `services/workspaceConnection.ts` and `workspaceProtocol.ts` for authentication,
  parsing, multiplexing, flow control, cancellation, and PTY lifecycle.
- Extended `services/interactive.ts` for capabilities, revisions, save operations,
  and training submissions; typed errors/response models.

Do not turn the existing log-only `LogTerminal` into an interactive terminal by
appending strings to a PRE. Use raw byte-safe terminal transport with incremental
UTF-8 handling as appropriate, including multibyte characters split across reads,
PTY escape sequences, Ctrl-C/input, Unicode, and real RESIZE events from the fit
addon/ResizeObserver. Bound scrollback and disable unsolicited browser clipboard
control/link activation. Treat terminal output and filenames as untrusted data;
no `innerHTML`, script evaluation, active file previews, or auto-executed content.

Dispose editor models, xterm instance/addons, sockets, observers, timers, requests,
and listeners on route change/unmount/logout. Abort late callbacks using exact
workspace/runtime/generation/request identity. Preserve unsaved buffers across
temporary reconnect in memory, but clear sensitive content on logout/account
change. Browser storage must not contain tickets, shell input/output, file text,
or artifact capabilities; reload warnings do not imply guaranteed buffer recovery.

Workspace READY requires Gateway authorization plus valid workspace application
handshake/file capability, not just WebSocket `open`/Gateway `ready`. Terminal
status requires valid PTY OPENED. A naturally exited shell offers New Terminal;
it does not invalidate already-working file access or stop the runtime.

Persist durable workflow IDs/status in Scheduler and make them discoverable
from the workspace details/list. Reconnect/loading errors must not erase a known
in-progress server-side save or create another one. Do not show success for stale
generations or guess that Stop, save, or submission succeeded from a closed socket.

## 12. Verification required

### 12.1 Local development/unit/component checks

Keep production secrets disabled; use fake Docker/storage/registry/management
adapters and controlled clocks/transports. Run affected Scheduler/Worker/Builder/
Access/Gateway/Management/Object_store/UI suites and previous build/runtime
regressions. Ensure the UI test script includes all editor/protocol/workflow tests.

Required tests include:

- Connect only enables Open Editor for the current verified runtime; direct
  route/reload gets fresh authorization, and wrong-owner/stale grants fail closed.
- Explorer operations actually invoke bound workload file service; traversal,
  symlink swaps, hard links, FIFOs, binary/oversize files, bad UTF-8, incomplete
  writes, excess directory entries, permissions, and cancellation are covered.
- Two editor writes with one version cannot both succeed; known terminal edits
  before Save File trigger conflict and preserve the dirty buffer.
- Workspace records handle every header/payload split, coalescing, malformed or
  duplicate metadata, invalid state/order, truncated EOF, replayed request IDs,
  large chunks, slow readers, and bounded multiplexing with terminal output.
- Real terminal adapter OPEN/bytes/resize/CLOSE/EXIT cleanup behavior is preserved;
  New Terminal works without closing file access; disconnect reaps only its PTY.
- Capture gates/drains mutations, closes PTY, pauses boundedly, restores metadata,
  persists exact IDs, streams uploads, and leaves heartbeat/new-work counts correct.
- Save succeeds only after accepted durable artifact and digest publication;
  storage/registry failure, ambiguous responses, late callbacks, Stop/lease loss,
  and Worker restart do not create stale revisions or clear holds.
- Private artifact routes cannot be bypassed through generic object APIs;
  wrong Worker/attempt/key/version/hash/size or expired capabilities are rejected.
- Builder SNAPSHOT import is bounded/fenced and source identity verified; original
  safe metadata survives, runtime keepalive/secrets/volume config does not.
- Submitted training image executes the chosen command rather than keepalive,
  uses the edited project, and pulls its final digest. Original job/source image
  remains immutable. Unique per-job manifests/provenance survive repeated jobs.
- Submission before successful publication/release cannot create a claimable job;
  retries/double clicks/restarts create one job, and failures remain recoverable.
- UI flushes all buffers before capture; conflict/failure blocks Save/Submit;
  live file save is clearly distinct from durable save; history/restart/reload,
  terminal exit, Stop, logout, offline, and workflow progress are covered.
- Secret scans of schemas/test logs/recorded Docker arguments/browser storage
  show no tickets, credentials, capabilities, or raw content leaks.

### 12.2 Real PostgreSQL gate

Extend the disposable-schema harness to upgrade through 003, run migrations twice,
and exercise constraints/immutability and real concurrency. Test simultaneous
save requests, Save/Submit/Stop races, accepted artifact/publish callbacks,
workspace saved-head ordering, multiple reconciler processes, capture lease
takeover, and exactly-one job creation after assignment release. SQLite cannot
validate these locking behaviors alone.

### 12.3 Real Docker/full application gate on separate Ubuntu host/CI

The editing machine is not a runtime host. Extend `test/interactive_e2e/runtime/`
with a production-workspace scenario using real Scheduler claim/controller,
Worker broker/file helper/snapshot journal, artifact storage, Builder, disposable
registry, Headscale/Tailscale/Gateway/Access, and Chromium. Keep the old fake-PTY
suite as a separate protocol gate. The broker-only CPU fixture is also not this
full application gate.

Use a deterministic digest-pinned tiny Python fixture and an explicitly named
CPU-only test adapter/profile for portable CI if no GPU is available. Production
GPU eligibility must remain unchanged and must never silently fall back to CPU.
Exercise production paths for file operations, snapshots, authorization, Builder
publication, job provenance/command, and cleanup; never substitute fake files or
fake successful saves. Provision disposable scoped secrets/CA, no production data.

Browser scenario:

1. Start -> assigned -> READY -> Connect -> Connected successfully -> Open Editor.
2. Expand actual project, create directory/file, edit existing file, Save All,
   and verify contents/User/WORKDIR inside the workload.
3. Run terminal commands that create/modify another file; verify bidirectional
   PTY, Ctrl-C/resize, Refresh/conflict behavior, and shell restart.
4. Save for Later; wait for real capture/storage/push/digest/revision success.
5. Stop, prove exact reservation release, start saved revision on another Worker
   where available, and verify edited/deleted/terminal-created files and a local
   installed fixture dependency survive.
6. Make another edit and Submit for Training with a distinguishable Python
   command. Prove saved revision, interactive stop/release, normal job creation,
   exact derived image/CMD, and normal estimation/training/output path.
7. Repeat/reload/double click and prove no duplicate revision/job or leaked PTY.

Include component restarts/failures during file writes, commit/pause, upload,
publication and submission. Verify private artifact isolation, image/mount/env
boundaries, health/session revocation, late completion cleanup, and survival of
an unrelated sentinel container/image/volume/Headscale node. Emit sanitized
diagnostics/JUnit, never archives, image inspect/env dumps, raw terminal bytes,
source contents, secret databases, or tokens.

### 12.4 Real GPU/multi-host acceptance

On the supported GPU Worker host use an image from the existing builder and a
real private registry. Verify assigned physical GPU only, actual writable quota/
CPU/RAM/PID budgets, persisted dependency/files, estimation and batch execution
from the published edited digest, output/checkpoint behavior, and plan2 priority/
exclusive-placement/heartbeat guarantees during editor use and capture.

Keep Gateway/control and Worker on separate machines/networks. Use a browser
without Tailscale through public HTTPS/WSS. Verify artifact URLs are reachable
only by authorized roles, outbound Worker behavior, NAT routing, lease-loss/
SIGKILL/Docker-restart cleanup, and storage/registry/management outage recovery.
Label actual, skipped, and pending checks honestly; keep feature admission off
until real infrastructure gates pass.

## 13. Implementation order and host operations

Implement in reviewable stages, keeping new editor/save/submission flags off:

1. Write protocol/storage/provenance specifications and tests for invariants.
2. Add 003 models/migration and durable save/submission/artifact state machines.
3. Implement private artifact authorization/streaming/receipts and namespace guards.
4. Implement workload file helper, workspace broker/Access protocol, capability
   readiness, persistent terminal lifecycle, and backward-compatible grants.
5. Build the editor route/explorer/text editor/terminal with real transport.
6. Implement journalled exact snapshot capture/reconciliation and Builder SNAPSHOT
   publication, then connect Save for Later/history/start-from-revision UI.
7. Implement executable snapshot-source batch derivation, digest provenance,
   release-gated submission orchestration and normal job links/status.
8. Run local/component/PostgreSQL gates, add/run full Docker/browser CI, then
   perform actual GPU/multi-host acceptance on the designated hosts.
9. Finish exact deployment/upgrade/rollback commands and implementation handoff.

Add operator flags such as `WORKSPACE_EDITOR_ENABLED`, `WORKSPACE_SAVE_ENABLED`,
and `WORKSPACE_TRAINING_SUBMISSION_ENABLED`, default off. Capability/version pins
must allow legacy terminal clients/runtimes during rollout. Flags stop new
admission; do not abandon authorized in-progress capture/publication/cleanup.

Document editor compatible images/root/interpreter, record/file/queue limits,
pause/capture/upload/publication deadlines, artifact space/headroom/max size,
private artifact URLs/CA/service credentials/capability signing, registry Builder
roles, image retention, original launch metadata, saved revision/history behavior,
and the exact supported batch command grammar.

Use host-installed Ubuntu Worker plus existing independent systemd lease guard.
Identify which host owns every path/socket/temp artifact and avoid cross-host
`localhost` URLs. Extend supervision to track snapshot side effects and bounded
pause recovery; do not imply nested-Docker deployment is validated.

Provide exact commands per role in `docs/interactive-workspace-operations.md`:
database backup/migration, private bucket/versioning/namespace protection, service
secret provisioning/rotation, Worker/Builder upgrades, pinned Access publishing,
Node UI build/proxy/CSP deployment, protocol/file/snapshot preflight, full gates,
flag enablement, failure diagnostics, and reference-aware retention/cleanup.

Rollback: disable new saves/submissions/editor admission, reconcile captures and
accepted uploads/publications, drain/stop runtimes, confirm cleanup/release and
created batch provenance support, then roll back processes. An older Worker or
Builder cannot manage live new-format snapshot tasks. Do not delete saved
revisions/artifacts to make rollback appear successful.

## 14. Completion checklist and handoff

Completion requires all of these implemented behaviors:

- Open Editor after successful Connect, with fresh server authorization and
  usable direct-route/reload/error states.
- Real create/edit/save file operations and an interactive web terminal inside
  the exact workload User/WORKDIR, with bounded streams and exact PTY cleanup.
- Durable Save for Later creates a published immutable revision that restores
  edited files and filesystem-installed dependencies on a later runtime.
- Submit for Training captures the current changes, safely stops/releases the
  interactive assignment, and creates one normal batch job whose executable
  image runs the chosen command using the exact saved contents.
- All original scheduling/exclusivity/leases/owner/Origin/grant/generation/cleanup
  guarantees still hold during editing, snapshots, failures and retries.
- Saved source/history and original jobs are immutable; failures retain usable
  saved revisions and never falsely report saved/ready/queued success.
- No credentials, host filesystem, Docker daemon, or privileged controller role
  becomes accessible through the editor/file/terminal/snapshot interfaces.
- Meaningful local, PostgreSQL, full Docker/browser, and real host acceptance
  results are clearly separated, with admission gated on the actual host results.
- An implementation handoff (e.g. `changes4.md`) lists changed files/contracts,
  migration/config/deployment commands, test results, retained limitations,
  rollback behavior, and host checks still pending without claiming they ran.

## 15. Primary implementation references

Verify library/API behavior against official documentation during implementation:

- [Monaco Editor](https://microsoft.github.io/monaco-editor/) and its official
  repository/docs for Vite worker bundling and model disposal.
- [xterm.js security guidance](https://xtermjs.org/docs/guides/security/) for
  treating terminal integration as sensitive application code and locally
  bundled trusted assets.
- [Docker container commit](https://docs.docker.com/reference/cli/docker/container/commit/)
  for paused filesystem capture, mounted-volume exclusion, and image config
  changes; verify exact Engine SDK behavior on the supported host version.

Repository transport/operations documents remain authoritative for this
application's identity, isolation, readiness, leases, and cleanup contracts.
