# Durable interactive workspaces and batch training: implementation handoff

Status: implementation plan, not a claim that this feature works today.

Prepared against the repository on 2026-09-23. Reinspect the checkout before implementation; preserve unrelated changes. The requested outcome is a working user workflow, including deployment configuration and acceptance evidence. Adding endpoints, buttons, or flags alone does not complete this work.

## 1. User outcome and scope

An authenticated user must be able to:

1. Select an existing owned batch job image, or create a workspace from an upload.
2. Start an interactive runtime with the existing browser editor and terminal.
3. Modify code and install Python packages into a supported writable environment.
4. Choose **Save for Later**, wait for a durable revision, stop the runtime, and return later on any compatible Worker with the code and installed packages intact.
5. Alternatively, choose **Submit for Training**, supply a training command, and have the platform save a revision, release the interactive runtime, and create exactly one normal batch job using that revision.
6. Restart interactive work from the saved revision after training, independent of the lifetime or location of the original container.
7. Repeat save, restart, modify, and training cycles without overwriting the original image or losing earlier published revisions.

Implement the full path through UI, Scheduler, Worker, object storage, Builder, registry, estimation, training, and deployment. Keep existing archive-based batch jobs and terminal-only interactive runtimes working.

This release supports filesystem snapshots and Python training commands. It does not restore processes, GPU memory, open terminals, or shell-only environment changes. Training checkpoints/output remain in the existing output storage workflow. Training output does not automatically mutate the saved workspace revision.

System package installation with `apt`, arbitrary root shells, distributed launcher commands, automatic Dockerfile reconstruction, and general persistent volumes are outside this release. Keep the existing non-root workload policy. Do not silently broaden privileges to make package installation work.

## 2. Architecture decisions

### 2.1 Images are immutable; saving creates a new revision

Do not attempt to write installed packages into an existing lower image layer. A running container has a writable filesystem above its immutable image layers. Capture that filesystem as a new image revision.

```text
Owned batch image B0, resolved to a digest
  -> initial portable workspace revision W0
       -> interactive workload container + separate access/network services
            -> user changes code and installs packages
                 -> committed workload image W1
                      -> private artifact -> Builder -> registry digest D1
                           +-> Save for Later: workspace saved head = W1
                           |     -> later interactive runtime from D1
                           +-> Submit: executable image T1 FROM D1
                                 -> estimate -> schedule -> train
```

The interactive access image is already a separate service image. Keep access agents, broker credentials, and Tailscale state outside the workload. Capture only the exact workload container. There is no need to bake interactive access software into each saved training environment.

### 2.2 Chosen batch integration: a small executable child image

Use the saved workspace digest as the source of a lightweight batch image whose only intended changes are execution configuration: cleared interactive entrypoint, explicit Python `CMD`, user, working directory, and approved persistent environment metadata. Do not reinstall dependencies or recopy the project.

This fits the current executor, which normally runs an image's default command. Store both the workspace source digest and the executable digest. The initial job state is `NOT_RUNNABLE`; a dedicated snapshot-source Builder branch creates the executable image and advances it to `VRAM_ESTIMATION_PENDING`. The archive extraction/build branch must never see this job.

Training runs the exact executable digest. No fallback to `<job>:latest` is permitted for workspace-sourced jobs. Resume attempts use the same digest and explicit validated resume arguments.

### 2.3 Chosen artifact transport: private, versioned S3/MinIO bucket

The Scheduler controls a dedicated private snapshot bucket using a protected, bucket-scoped service credential. It issues bounded, attempt-specific multipart upload capabilities to the assigned Worker and version-specific download capabilities to an authenticated Builder. The Worker does not receive registry push credentials or general storage credentials.

The Worker streams `docker save` through gzip into a bounded local spool file, calculating compressed size and SHA-256. It then uploads the file in bounded multipart chunks. The Builder streams the accepted artifact into a bounded spool file, verifies it, and loads it through a streaming Docker interface. Whole-image buffers in Python are prohibited.

A local spool is deliberate: it allows known size/hash, reproducible upload retries, and multipart retries without recapturing a changed container. This uses temporary disk; account for it explicitly. Do not confuse a spool file with a completed upload.

S3 presigned URLs are bearer capabilities and can be reused until expiry; do not describe a raw URL as single-use. Achieve effective single-artifact publication with an immutable upload attempt, server-owned key, completed multipart identity, storage version, accepted receipt, and database fencing. Late PUTs or unaccepted versions must never change the artifact a Builder reads.

## 3. Current implementation: reuse and defects to fix

Use implementation files as the source of truth. `INTERACTIVE_ACCESS_FLOW.md` describes an older SSH/privileged-container design and must not drive this implementation.

| Area | Existing implementation | Required work |
| --- | --- | --- |
| Initial image creation | `Docker_Image_Builder/interactive_build.py` derives from existing job image or upload | Preserve digest resolution and fix the writable Python environment contract |
| Runtime start | `Scheduler/app/services/interactive_runtime_service.py` prefers `saved_revision_id` | Validate selectable revisions, readiness, ownership, active saves, and expose actual saved head |
| Save admission | `workspace_editor_service.py` creates `REQUESTED` records | Dispatch, atomic admission, durable idempotency, failure/recovery, completion |
| Training admission | Same service inserts a submission in `SAVING` | Complete reconciliation through runtime release and exactly one job |
| Capture | `Worker/interactive/snapshot.py` has commit/export helpers | Integrate into Manager; correct Docker calls; real upload; reconciliation |
| Worker control | `managed_worker.py`, `scheduler_protocol.py`, Scheduler `scheduling/claims.py` | Deliver and acknowledge fenced save commands while heartbeats continue |
| Artifact API | `snapshot_artifact_service.py` and internal routes | Replace metadata placeholders with real storage capabilities and verified receipts |
| Snapshot publication | `interactive_build.py` has a `SNAPSHOT` branch | Bounded streams, exact identities, cancellation, config validation, publication callbacks |
| Published head | `interactive_workspace_service.mark_ready()` writes the head | Atomic save completion and compare-and-swap head advancement |
| Job provenance | Migration 003 contains source fields | Add corresponding ORM/schema/service/Worker support; archive source is currently mandatory |
| UI | `WorkspaceIDE.tsx` has Save/Submit callbacks | Capability gating, flush edits, fresh action keys, settings form, durable status recovery |

Known defects that must have explicit regression coverage:

- The runtime Manager never invokes the snapshot capture module. Current heartbeats deliver only renew/stop decisions.
- `_upload()` writes a local gzip file; `complete()` merely logs and returns `True`.
- Current artifact completion accepts claimed size/hash without checking storage. It rejects identical retries after first acceptance and omits full Worker instance/lease checks.
- There is no controller moving saves to `SUCCEEDED` or submissions to `JOB_CREATED`.
- `create_submission()` calls a committing `create_save()`, leaving a crash window with an orphan save and no submission. Refactor into one transaction.
- Existing replay checks occur after runtime readiness checks, so a valid retry after successful Stop can fail. Lookup an owned operation and compare its request hash before requiring a currently live runtime for a new operation.
- `mark_ready()` unconditionally advances workspace pointers; its comments promise stronger stale-publication protection than its code provides.
- `snapshot.py` passes `timeout=` to `Container.pause()` and `unpause()`. Verify actual installed SDK signatures; use supported APIs and a bounded client/operation deadline. Permissive mocks currently hide this problem.
- Snapshot capture holds the container paused through export. Unpause promptly after the commit snapshot exists, with coordinated health checks and failure recovery.
- `ops.remove_exact(record, image_id)` uses a container-removal helper with an image ID. Separate exact image cleanup from exact container cleanup.
- A full tag is passed as the positional repository argument to `commit()`. Pass repository/tag explicitly according to the installed SDK and assert the real resulting reference.
- Snapshot callbacks use `SNAPSHOT_UPLOADED`, which is not in the normal runtime event schema. Use separate snapshot progress events, not invented runtime phases.
- The Builder buffers up to 8 GiB in a `bytearray`, then copies it into `bytes`.
- The Builder uses one shared `dml-snapshot-clean:ready` staging tag across attempts, and its optional command-build path then retags the original image over the executable tag. Use attempt-specific references and the actual resulting image ID; consume/check build errors fully.
- Builder claims currently do not supply a training command to that optional branch. Replace the unused branch with the explicit batch derivation flow in this plan.
- SQL artifact size is `BIGINT` in migration 003, while the save ORM uses `Integer`. Align all byte counts and schemas for multi-GiB artifacts.
- The UI caches one save key for the entire runtime generation. A later explicit Save would replay an earlier capture. Retries of one action reuse a key; a new action requires a new key.
- Durable Save/Submit do not flush dirty editor buffers first. Submit hardcodes `python train.py` and does not track the job to completion of submission.
- Batch `_container_user_args()` overrides the image user with the host UID and sets `HOME=/tmp`. This can break packages installed in the interactive user's home. Estimation also needs writable report mounts for a non-root image.
- The default Worker runtime lifetime is ten minutes. Save must not race uncoordinated timeout cleanup, and the usable deployment example must configure both lifetime limits.
- Owner training settings currently accept `HIGH`; enforce existing authorization rules so ordinary users cannot grant themselves administrative priority.

## 4. Product behavior and invariants

### 4.1 Save for Later

- Normal editor Save writes live files only. The UI must distinguish this from a durable revision.
- Durable Save first flushes all dirty files using their existing version checks. Any conflict or failed write cancels the new snapshot request and identifies the file.
- Save captures one point-in-time filesystem state. Display capture start/completion and explain that edits after capture require another Save.
- Keep the runtime alive after a successful Save. Offer separate Stop and Restart from Saved actions. Multiple saves in one runtime are supported.
- Briefly gate new editor mutations and terminal input while obtaining a consistent capture. Use a runtime-wide gate across all connections. A Docker pause is filesystem consistency assistance, not application-level transactional consistency: users should finish package installs and other multi-file operations before saving.
- After the image commit completes, ordinary Save may resume editing during export/publication. Do not claim later changes belong to that earlier save.
- Allow at most one active save per runtime/generation, including TRAIN saves. An overlapping request gets the active operation ID and an actionable conflict.
- A failed save leaves the last successfully published revision available. Never update the saved head on failure or partial upload.

### 4.2 Submit for Training

- Show job name, training command, optional resume command, and permitted priority settings. Default fields may be populated, but the actual submitted command must be visible/editable.
- Flush editor buffers; atomically create one submission and its TRAIN save.
- After capture, keep the runtime gated against new mutations while the submission proceeds. If the save fails and the runtime remains usable, reopen editing and show the error. If runtime shutdown has begun, do not pretend editing can resume.
- Publish the revision before initiating runtime Stop. Wait for existing assignment cleanup AND management revocation/release before creating a job that can consume Worker resources.
- Create exactly one job in the same database transaction that records `submission.job_id` and `JOB_CREATED`.
- `JOB_CREATED` means a batch job exists; report its real build/estimation/queue state. It does not mean training has started or completed.
- If executable image preparation or training later fails, link to that job and preserve the saved revision. Retrying image preparation must reuse the existing job; a user-requested new training run uses a new submission key.
- Successful submission leaves the saved workspace revision reusable for later interactive work, including when training fails.

### 4.3 Revisions, lineage, and ordering

- `runtime.revision_id` is immutable and identifies the image from which that runtime started. Never mutate it to pretend a running container restarted from a newly saved image.
- `Save.parent_revision_id` records this launch source. Repeated commits from the same container may therefore be sibling images at the Docker layer level. That is valid.
- Add `expected_saved_revision_id` or equivalent publication version to the save operation, captured under the workspace lock when it is admitted. Use it for compare-and-swap head advancement.
- Store operation ordering separately from Docker ancestry. Allocate revision numbers under a workspace lock, not an unlocked `max()+1`.
- A successful new save from the same runtime uses the current saved head as its expected predecessor, even though the runtime launch revision remains unchanged.
- For this release, reject a new runtime start while any workspace save/publication/submission is nonterminal. This avoids racing a replacement runtime against an outstanding old capture. Existing reads and terminal-only compatibility behavior must still work.
- Owner revision history can select any owned `IMAGE_READY` revision. Starting an older revision must be explicit and must not silently rewrite the saved head. A later explicit save from it can advance the current saved head with a recorded expected-head comparison.
- Identical callbacks after success return the original result; mismatched hashes/digests/capture attempts fail with a conflict. Late stale callbacks cannot overwrite a newer head, create duplicate revisions/jobs, or resurrect failed/cancelled work.

### 4.4 Stop, lease loss, and timeouts

- Stop before artifact acceptance cancels in-flight capture/upload; abort only that upload and preserve the previous head. If the user chooses to discard, make that consequence explicit.
- Once the Scheduler accepts an immutable storage receipt under a valid Worker fence, publication may finish independently of the Worker/runtime. Record that boundary durably. Worker loss after acceptance must not discard valid portable bytes.
- Exact callback retries for an already accepted receipt can return the stored result after runtime release, subject to authenticated identity and exact request matching; they do not authorize new capture/upload work.
- Block startup from a still-publishing revision. A runtime can always start from an earlier ready revision after the relevant operation is terminal.
- Do not extend a lost lease to rescue a save. Do not let a save thread block heartbeats or Stop.
- Give capture, upload, publication, and cleanup separate bounded deadlines. A frontend network interruption must not cancel a valid backend save.

## 5. Phase 0: inspect, establish baselines, and define contracts

1. Read applicable `AGENTS.md` files, this plan, current source, and deployment manifests. Keep a short implementation checklist with completed phases and actual test evidence.
2. Inspect existing dirty changes before editing. Do not reset the checkout or restore the old deleted plan.
3. Read the following code together, not as isolated modules:
   - `Scheduler/app/models/{interactive_workspace_model,interactive_runtime_model,job_model}.py`
   - `Scheduler/app/services/{workspace_editor_service,snapshot_artifact_service,interactive_workspace_service,interactive_runtime_service,interactive_controller,job_service,image_builder_service}.py`
   - `Scheduler/app/services/scheduling/{claims,policy,config}.py`
   - `Scheduler/app/api/{interactive_runtime_route,interactive_workspace_route,worker_execution_route,jobs_route}.py`
   - `Worker/{managed_worker,scheduler_protocol,execution_state,executor,vram_estimation}.py`
   - `Worker/interactive/{manager,snapshot,docker_ops,broker,workspace_broker,cleanup}.py`
   - `Docker_Image_Builder/{builder,interactive_build,interactive_api,docker_ops,api,database}.py`
   - `Object_store/{main,init_buckets}.py`, UI interactive services/pages/IDE, migrations 001–003.
4. Run existing focused tests before changes. Record baseline failures separately from new failures. Use separate component processes to avoid Python module-name collisions.
5. Pin/record the Docker SDK and Engine versions used for acceptance. Replace mocks that accept any method/argument with signature-aware fakes where appropriate.
6. Write the precise save-command, progress, artifact receipt, Builder descriptor, capability, and submission contracts. Update schemas before independent components are implemented.

Exit: the agent can trace every hop and has a concrete compatibility strategy for old Workers/Builders, rather than assuming flags prove capability.

## 6. Phase 1: schema, state machines, and admission

Add an additive, idempotent `004_durable_workspace_workflow.sql` migration. Update ORM definitions and API schemas to match it. Migration 003 is foundation only; do not rely on `create_all()` to upgrade production.

Required schema work:

- Map a real `WorkspaceSnapshotArtifact` model to the existing artifact table. Record operation, capture attempt, upload attempt, storage bucket/key/version, compressed size/hash, uncompressed budget/observations, image config ID, platform, and state. A storage version is unique with its bucket/key, not necessarily globally unique across providers.
- Use `BIGINT`/`BigInteger` for all artifact byte counts. Add foreign keys and uniqueness constraints for operation-to-artifact and operation-to-revision relationships.
- Store immutable Worker `instance_id`, assignment ID, attempt token, runtime generation, parent revision, expected saved head, capture attempt, deadlines, and safe failure codes on save state or its associated attempt table.
- Add the active-save partial unique index to ORM-backed test schemas too. Serialization must work with multiple Scheduler instances, not just one Python lock.
- Protect published image identity/provenance and accepted artifact identity from mutation. Audit existing immutable triggers and extend their field lists. Migration 001 is rerun at startup and recreates its function; migration 004 must reliably install the final definition on every startup.
- Add a same-workspace foreign key for `saved_revision_id` and publication ordering constraints where practical.
- Implement Job source fields already anticipated by migration 003: `source_kind`, `source_workspace_id`, `source_revision_id`, `source_image_digest_ref`, `executable_image_digest_ref`, plus normalized command argv and environment metadata if needed.
- `ARCHIVE` jobs require archive/object/base inputs. `WORKSPACE_REVISION` jobs require owned ready source revision/digest and do not require an archive. Make `object_key` nullable with source-specific constraints; update all callers/serializers that assume it is a string.
- Keep archive defaults and existing rows compatible. Add a database-enforced unique submission/job relationship; avoid placeholder fake archive keys.
- Prevent ordinary users from requesting administrative `HIGH` priority through the new API.

Implement these state transitions with explicit allowed predecessors:

```text
Save:
REQUESTED -> CAPTURING -> UPLOADING -> PUBLISH_QUEUED
          -> PUBLISHING -> SUCCEEDED
nonterminal -> FAILED or CANCELLED (according to the accepted-artifact boundary)

Revision:
QUEUED -> BUILDING -> IMAGE_READY
BUILDING -> QUEUED on bounded retry, otherwise FAILED

Submission:
SAVING -> WAITING_FOR_REVISION -> STOPPING_RUNTIME
       -> WAITING_FOR_RELEASE -> PREPARING_JOB -> JOB_CREATED
nonterminal -> FAILED
```

`PREPARING_JOB` prepares the durable job record; the job's own build state tracks the executable child-image build after `JOB_CREATED`.

Refactor services to use internal helpers that flush but do not commit. Save creation and training-submission creation must each be one transaction. Follow the existing Worker-before-runtime lock order when those records are involved; define a consistent workspace/save/submission lock order and test races. Network calls run outside database transactions, followed by a conditional update against the same operation token/state.

Expose owner-scoped APIs to recover active and recent save/submission operations after a browser reload. Include capability flags and disabled reasons in runtime/workspace responses. Availability requires operator flags, compatible assigned Worker, writable environment support, and configured storage; `editor_capable` alone is insufficient.

Exit: admission, replay, ownership, concurrent-save serialization, and schema upgrades pass real PostgreSQL tests.

## 7. Phase 2: one portable Python environment across modes

Make installing a Python dependency useful before implementing a success message for saving it.

Adopt this default contract for newly built workspace images:

- Effective user/group `10001:10001`, with a matching passwd entry and a writable explicit `HOME` such as `/home/dml`.
- Project at `/workspace`, owned by that user, in the workload writable layer.
- `python -m pip install --user ...` supported in that user's site directory; preserve the base interpreter and its installed PyTorch/CUDA packages.
- Add the user executable directory to image `PATH`. Preserve relevant base image environment needed for Python/Conda/CUDA. Do not blindly replace it.
- Document the supported package command in the UI. Test installing and overriding a dependency, including an executable script. If a base disables user-site packages or ignores this environment, reject it with a clear compatibility reason or implement/test a deliberate writable environment overlay before admitting it.
- Keep package/environment directories outside the batch output mount. A user-created virtual environment may be supported only when the selected interpreter and persistent paths are explicit and tested; shell activation alone is not captured configuration.

For older ready workspace images, never mutate an existing digest. Detect whether the environment meets the contract. Either allow it after checks or provide a new derived environment revision through the Builder. Existing sessions can continue with their pinned compatibility mode; new save capability must be truthful.

For `WORKSPACE_REVISION` jobs, preserve image user, HOME, PATH, interpreter, and working directory through estimation, initial training, and resume. Do not use the legacy host-UID/HOME override for these jobs.

Fix output/report mount permissions for that image user. The current estimator creates a report directory through `TemporaryDirectory`, commonly mode 0700 owned by the Worker; a UID 10001 container cannot write it without preparation. The current batch path seeds and binds the image working directory; preserve code and ownership without hiding the persisted Python environment. If seeding fails, fail clearly for snapshot-source jobs instead of silently training against an unrelated fallback directory. Avoid broad recursive chmod/chown outside an exact job directory and do not follow user-controlled symlinks into other paths.

Normalize commands to explicit argv. The chosen interpreter must be used by estimation as well as training; remove heuristics that accidentally strip meaningful Python arguments. Initially support `python`/`python3` plus a script under the workspace and ordinary arguments; preserve the current restricted command scope. Validate resume commands the same way. A Python allowlist is not a sandbox—the workload already runs arbitrary user Python—but it avoids accidental shell interpretation and keeps estimation predictable.

Exit: a real container running as UID 10001 installs a fixture package; fresh containers using the same environment contract can import it and write outputs/report files.

## 8. Phase 3: Scheduler-to-Worker dispatch and capture

### 8.1 Negotiate and deliver commands

- Extend Worker registration/inventory with explicit snapshot protocol support. Use a backwards-compatible optional field defaulting to unsupported or a versioned API change; old strict-schema peers must not receive unknown required fields.
- Advertise actual capability only after Worker startup validates its capture implementation, spool path, storage connectivity, and required SDK support.
- Deliver pending save commands in a bounded heartbeat response list or a dedicated authenticated poll endpoint tied to the live assignment. Choose one path and test its retry behavior. This plan prefers heartbeat delivery alongside renewal decisions.
- Include operation ID, capture attempt, workspace/runtime/generation, assignment/instance/attempt identity, expected source revision, and deadlines. No browser-selected container ID, host path, or arbitrary Docker arguments.
- Add explicit progress/completion/failure endpoints using snapshot schemas. An acknowledgment only means the Worker journaled the command; it does not mean an image is durable.
- In `ManagedWorker`, deduplicate by operation/capture attempt and dispatch into the running `Manager`. Capture/export/upload work uses a bounded background executor. Heartbeats, broker I/O, and Stop remain responsive.
- Journal operation state without read-modify-write races that overwrite newer assignment/container records. Prefer Coordinator atomic updates under its existing lock.

### 8.2 Capture protocol

1. Recheck authoritative lease, desired runtime state, identity labels, and actual workload ID.
2. Journal operation/capture identity before side effects. Reuse the same committed image on retries; never recapture changed content under an accepted operation ID.
3. Acquire the runtime-wide mutation gate; drain in-flight editor writes and reject/queue new ones with a visible saving state. Coordinate all connections, not just the triggering browser.
4. Reload container metadata. Reject unsupported volumes and unapproved binds/tmpfs affecting persisted paths. Capture actual platform, image ID, user, workdir, and approved environment; do not hardcode guessed values in Scheduler receipts.
5. Commit the exact workload using supported Docker APIs, explicit repository/tag, and pause semantics tested against a real daemon. Use a dedicated client/timeout suitable for large commits. Bound the pause window and handle a timeout as an uncertain operation until reconciled.
6. Unpause immediately after a successful commit. Ordinary SAVE releases the mutation gate; TRAIN keeps mutations blocked until success/failure resolution according to section 4.
7. Export the immutable image to a size-limited spool. Bound compressed bytes, archive expansion, disk space, duration, and concurrent operations. Keep checking lease/cancellation while work still depends on Worker authority.
8. Upload according to Phase 4 and obtain an accepted receipt. Preserve the image/spool until the exact receipt or terminal failure is known.
9. Remove only exact operation-owned local tags and artifacts when safe. Do not delete shared parent images or images used by other attempts. No global Docker prune.

Coordinate health and deadlines: a controlled short pause must not look like `HEALTH_FAILED` to Manager/Scheduler, but genuinely dead workloads must still be stopped. A transient commit pause cannot suspend lease enforcement. Add a maximum capture grace tied to the operation token. Do not hold the workload paused during compression or network upload.

### 8.3 Crash recovery

- On Worker startup, inspect journaled operations before normal assignment cleanup removes their workload/image references. Reconcile exact IDs/labels and accepted receipts.
- A lease lost before acceptance terminates mutation authority; late local work may finish at the daemon but cannot publish without valid acceptance. Clean exact orphan objects after resolving uncertainty.
- A timed-out Docker call can still complete server-side. Record uncertainty, poll the exact staging reference/container status, and never start overlapping capture blindly.
- Integrate with the lease guard and `interactive.cleanup`, including recovery from process death while a container is paused. Never leave a workload indefinitely paused.
- If the artifact was already accepted, let Scheduler/Builder finish publication without requiring the old Worker to return.

Exit: real Docker capture preserves code/packages, unpauses on all tested paths, and survives lost responses without duplicate snapshots.

## 9. Phase 4: real private artifact handoff

### 9.1 Storage isolation

Create a dedicated bucket, for example `workspace-snapshots`, with versioning and no anonymous access. Scheduler credentials are limited to this bucket and the operations needed for multipart control, metadata inspection, exact reads, and cleanup. Workers/Builders get only operation-specific URLs.

The current Object Store generic endpoints can access arbitrary bucket names using powerful credentials. Merely choosing a new bucket name is insufficient. Explicitly reject the reserved snapshot bucket/prefix in every generic upload/download/list/presign route, or restrict the generic service credentials so it cannot access that bucket; preferably enforce both. Test bypass attempts. Keep normal archive/output APIs compatible.

### 9.2 Upload protocol

- Authenticate Worker calls with existing Worker credentials. Validate Worker instance, assignment attempt, runtime generation, lease, operation state, capture attempt, and artifact policy.
- After export, Worker declares image config ID, actual portable config metadata, size/hash, and capture attempt. Scheduler validates limits and allocates an unpredictable operation/attempt-owned object key and multipart upload ID.
- Return presigned URLs for bounded batches of part numbers and a deadline. Support renewal under the same authorized upload attempt without changing captured bytes.
- Worker sends bounded parts, reports their receipts, and retries only failed parts. Never persist signed URLs in public logs or owner responses.
- Scheduler completes the multipart upload, reads back exact version/size/checksum metadata, and persists an immutable storage receipt. Do not treat multipart ETag as a SHA-256 digest. Where provider checksums do not establish the full digest, record it as Worker-declared until the Builder independently verifies every byte.
- Storage calls happen outside database locks. Completion retries reconcile existing object/version/upload state before deciding failure. Accept identical completion once and return the original revision/receipt for repeats.
- In one acceptance transaction, insert artifact record, allocate exactly one SNAPSHOT revision, link it to the operation, and move Save to `PUBLISH_QUEUED`.
- Protect against leaked/replayed upload URLs: only the accepted exact storage version can be read for publication. A later object version or conflicting checksum must not change it.

### 9.3 Builder download protocol

- Authenticate Builder descriptor requests and fence them to the current revision build attempt. Include exact artifact identity, required size/hash, image config ID, expected platform/config, and a short-lived version-specific download URL.
- Do not retain the current unrestricted `/objects/uploads/...` download path for snapshots.
- Support URL refresh during long/retried downloads without changing the artifact identity.
- A Builder must verify full compressed hash/size before Docker loading/publication. Reject missing hashes; tests must not remove verification metadata to manufacture a happy path.

### 9.4 Cleanup and retention

- Abort expired multipart uploads and remove abandoned spool files by exact operation/attempt identity.
- Retain artifacts through bounded publication retries; remove accepted staging artifacts only after registry publication and a configured recovery retention interval.
- Keep saved registry digests while referenced by a workspace revision, current saved head, active runtime, or job. Never let generic cleanup invalidate a user's saved revision.
- Container filesystem snapshots can include user-written credentials or caches. Access infrastructure secrets must remain outside the workload. Provide a concise product warning that files in the environment are included; do not promise automatic removal of arbitrary user secrets or layer history. Keep snapshot artifacts and resulting user images private unless an explicit sharing feature authorizes otherwise.

Exit: independent Worker and Builder hosts transfer a snapshot using only scoped capabilities; overwrite/replay/unauthorized bucket access cannot change accepted bytes.

## 10. Phase 5: Builder publication and Scheduler completion

- Implement streaming download to a bounded temporary file with compressed and expanded-size limits. Validate a single intended Docker image archive and its manifest/config/layer references; reject path traversal, multiple unexpected images, tag collisions, expansion bombs, and platform mismatch. Do not unsafely extract a user-controlled archive into the host filesystem.
- Use an SDK/CLI loading interface that accepts a file/stream without buffering the entire image. Validate real behavior and resident memory under a large fixture.
- Validate image config ID, platform, User, WorkingDir, volumes, approved environment, and operation-owned identity. Digest/config ID are different concepts: registry digest identifies the published manifest; image ID identifies image configuration. Store and compare the correct value at each boundary.
- Loading an archive can apply embedded repository tags. Export only controlled staging references, and validate/archive-normalize or isolate the load so it cannot overwrite unrelated local Builder tags. Do not trust a shared mutable staging tag across concurrent builds.
- Publish with the existing revision-and-attempt-specific repository/tag scheme and verify the registry digest. Keep cancellation/heartbeat checks during download, validation, load, tag, and push. Current `lambda: False` push cancellation is unacceptable.
- Extend the safe build-stage log allowlist with snapshot stages. Never expose capabilities, registry auth, or arbitrary environment secrets in stage logs.
- On ready callback, atomically mark the revision `IMAGE_READY`, save `SUCCEEDED`, and advance saved/current head with the expected-head comparison. Identical accepted callbacks return success. Stale attempts or a changed expected head cannot replace a newer saved revision.
- On terminal Builder failure, mark revision and save failed together, preserve prior saved head, and propagate failure to a waiting submission. Transient failures retain bounded retries and the immutable artifact.
- Add a durable reconciliation loop for stuck publication attempts and already-pushed images with a lost ready callback. Recover accepted work independently of browser/Worker lifetime.

Saved workspace image command metadata must be normalized deliberately: do not publish the runtime's `sleep` loop as an accidental batch entrypoint. Interactive startup already overrides entrypoint/command; the executable batch child explicitly clears the entrypoint and supplies the requested argv. Preserve portable environment metadata, not ephemeral access/assignment identity.

Exit: a saved revision can be pulled by digest on a clean second Docker host and launched interactively with the edited file and installed package present.

## 11. Phase 6: training handoff, executable build, and execution

Add a submission reconciler started by Scheduler lifespan, with cancellation/shutdown handling and database-backed claiming suitable for multiple API processes. Extend the existing controller or add `workspace_submission_controller.py`; do not store workflow progress only in a background task's memory.

1. Reconcile `SAVING`/`WAITING_FOR_REVISION` from actual Save and Revision state.
2. On snapshot success, request Stop idempotently through the existing runtime lifecycle. Repeated ticks must not rearm remote revocation indefinitely.
3. Wait for `WorkerAssignment.released_at` and confirmed terminal runtime cleanup. Respect existing Worker capacity accounting.
4. Validate owned source revision and immutable training settings, then atomically create one `WORKSPACE_REVISION` Job and set `JOB_CREATED`/`job_id`.
5. Extend batch Builder claims and validation to distinguish source types. For a workspace source, pull the exact saved digest and build a metadata-only Dockerfile, for example:

   ```dockerfile
   FROM <validated-saved-repository>@sha256:<digest>
   ENTRYPOINT []
   USER 10001:10001
   WORKDIR /workspace
   CMD ["python", "train.py", "--epochs", "10"]
   ```

   Render values from validated structured data and JSON-encode argv. Preserve the environment contract. Do not use an unvalidated string as a Dockerfile instruction.

6. Consume the build stream and inspect the actual resulting image. Push under an attempt-specific job tag; record the executable registry digest. Do not retag the source image over the child.
7. Advance to `VRAM_ESTIMATION_PENDING` through fenced Builder completion. Do not reuse the original batch image's VRAM estimate after the user changes code/packages.
8. Scheduler payloads and Worker execution must carry/use the immutable executable digest, source type, argv, and required environment contract. Snapshot jobs must not fall back to mutable tags on pull failure.
9. Fix estimator interpreter/report permissions and batch mount/user behavior as specified in Phase 2. Test checkpoint resume on a different Worker with the same installed package and user identity.
10. Keep Build and Training logs distinct and preserve links from workspace -> save/revision -> submission -> job -> source/executable digests.

Make failure messages specific: snapshot failed, publication failed, waiting for runtime release, executable build failed, estimation failed, training failed. Reconciliation retries must not create a second job or discard the published revision. A release that remains uncertain keeps the submission waiting with an actionable status; do not release GPU capacity based only on elapsed time.

Exit: a UI submission reaches exactly one real batch job and executes the selected command using the package installed interactively.

## 12. Phase 7: complete the browser workflow

Update `UI/User/src/services/interactive.ts`, `WorkspaceIDE.tsx`, title bar, interactive details/history pages, and relevant tests.

- Fetch explicit `can_save`, `can_submit_training`, disabled reasons, active operations, saved revision, and remaining session time. Buttons must reflect backend capability and in-progress state.
- Refactor editor save helpers so durable actions await a known successful flush of all dirty buffers. Do not snapshot after a conflict or concurrent editor version mismatch.
- Use one idempotency key per logical action. Persist operation ID/key/settings for recovery; retry the same action with that key after transport errors. Generate a fresh key after an action is terminal and the user explicitly initiates another action.
- Recover progress from server-owned operation lists after reload or a new browser session. Session storage is a convenience, not the only discovery mechanism.
- Poll both save and submission state with bounded backoff, abort on unmount, and stop at terminal states. Do not leave unbounded timers or silently swallow permanent status failures.
- Save success requires server `SUCCEEDED` plus a ready target revision. Display saved revision number/time and offer Stop or Restart from Saved. Save can leave the live runtime running.
- Add a submission form with visible editable command/settings; route/link to the resulting batch job once it exists. Show the real handoff phases until then.
- Expose revision history and explicitly starting an older ready revision. Ensure a failed newest attempt does not hide the last usable saved revision or disable Start unnecessarily.
- Stop with dirty buffers or live changes explains what has and has not been saved. During an active save, offer waiting versus explicit discard/cancel with server-enforced semantics.
- Do not label all live changes as durably saved merely because files were flushed. Terminal commands may modify files/packages outside editor awareness; state this precisely without falsely claiming a perfect dirty detector.
- Display the supported install command and online/offline state. A new runtime is required for changed pinned network/editor settings. Package installation failures must not be mistaken for snapshot failures.
- Warn before runtime expiry and disable starting a capture if there is insufficient configured time; never advertise an automatic save that does not exist.
- Preserve terminal-only compatibility and existing editor socket behavior. Extend `workspace-stream-v1` state messages compatibly if mutation-gate feedback is needed.

Exit: a user can follow the entire flow without manually calling internal APIs or copying image digests between forms.

## 13. Configuration: existing settings and additions to implement

The file is **`Scheduler/.env.runtime`**, loaded by `Scheduler/compose.runtime.yaml`. It is not a repository-root `env.runtime`. Preserve existing deployment-specific endpoints/secrets; examples below use placeholders.

### 13.1 Existing settings required for usable sessions

After implementation and acceptance, the following non-secret settings enable the feature:

```dotenv
# Existing Scheduler/.env.runtime settings: enable only after acceptance.
WORKER_NEW_WORK_ENABLED=1
INTERACTIVE_RUNTIME_ENABLED=1
WORKSPACE_EDITOR_ENABLED=1
WORKSPACE_SAVE_ENABLED=1
WORKSPACE_TRAINING_SUBMISSION_ENABLED=1

# Required for downloading packages from the terminal.
INTERACTIVE_INTERNET_ENABLED=1
INTERACTIVE_ALLOW_ROOT=0

# Example: two-hour session limit, not the current ten-minute example.
# Scheduler measures from assignment; Worker measures its active-session window.
# The earlier actual deadline still wins.
INTERACTIVE_LIFETIME_SECONDS=7200
INTERACTIVE_STARTUP_SECONDS=1920
WORKER_ASSIGNMENT_LEASE_SECONDS=45

# Retain/tune the actual GPU resource profile for available Workers.
INTERACTIVE_PLATFORM=linux/amd64
INTERACTIVE_CPU=2
INTERACTIVE_RAM_GB=8
INTERACTIVE_PIDS=256
INTERACTIVE_WRITABLE_GB=20
INTERACTIVE_PULL_HEADROOM_GB=40
INTERACTIVE_MIN_VRAM_GB=4
INTERACTIVE_PROFILE_VERSION=gpu-workspace-v2
```

These values are examples, not automatic capacity guarantees. Add snapshot disk reservations on top of workload and pull requirements; a full saved ML image can be much larger than its newly installed package. A Worker with insufficient spool/export capacity must reject Save with a clear reason before pausing.

Keep existing required values for `WORKER_CREDENTIALS_FILE`, `INTERACTIVE_MANAGEMENT_URL`, `INTERACTIVE_MANAGEMENT_CA_FILE` where needed, `INTERACTIVE_CONTROLLER_SECRET_FILE`, `INTERACTIVE_GATEWAY_WSS_ORIGIN`, `INTERACTIVE_GATEWAY_ID`, and `SCHEDULING_POLICY`. Continue mounting the Builder shared secret through `Scheduler/compose.interactive.yaml`.

### 13.2 Proposed NEW Scheduler settings — implement before documenting as usable

These names do **not** currently enable working behavior. Implement validated parsing, consumers, startup checks, example files, and tests for each one.

```dotenv
# Private S3 API origin, without bucket or object path.
# Must be reachable by Scheduler and by remote Workers/Builders for signed URLs.
WORKSPACE_SNAPSHOT_S3_ENDPOINT=https://snapshots.example.internal
WORKSPACE_SNAPSHOT_S3_REGION=us-east-1
WORKSPACE_SNAPSHOT_BUCKET=workspace-snapshots
WORKSPACE_SNAPSHOT_S3_CREDENTIAL_FILE=/etc/dml/snapshot-storage.json
# Optional; blank means system trust. Mount this file if configured.
WORKSPACE_SNAPSHOT_S3_CA_FILE=

# Server-enforced limits; bytes, not GiB strings.
WORKSPACE_SNAPSHOT_MAX_COMPRESSED_BYTES=17179869184
WORKSPACE_SNAPSHOT_MAX_EXPANDED_BYTES=68719476736
WORKSPACE_SNAPSHOT_PART_BYTES=67108864
WORKSPACE_SNAPSHOT_URL_TTL_SECONDS=900
WORKSPACE_SNAPSHOT_CAPTURE_TIMEOUT_SECONDS=120
WORKSPACE_SNAPSHOT_UPLOAD_TIMEOUT_SECONDS=3600
WORKSPACE_SNAPSHOT_PUBLISH_TIMEOUT_SECONDS=3600
WORKSPACE_SNAPSHOT_RECONCILE_INTERVAL_SECONDS=5
WORKSPACE_SNAPSHOT_STAGING_RETENTION_SECONDS=86400
```

Define the protected credential file format as JSON containing only `access_key` and `secret_key` for the scoped storage service account. Never commit real credentials. Read with protected-file validation consistent with existing services. Add an explicit optional session-token field only if required by the selected storage provider and tested.

Implementation requirements for these settings:

- `S3_ENDPOINT` is the actual S3 API, not the existing `/objects` proxy; signed URLs must not contain Docker-only DNS names or localhost on remote hosts. Use TLS verification and the configured CA in all clients.
- Scheduler storage control and signed URL issuance use this same tested endpoint to avoid signing a hostname that differs from the request hostname. If a deployment needs separate internal and transfer origins, implement and test explicit separate configuration rather than rewriting URLs after signing.
- Require versioning and private bucket access before save capability becomes available. Surface missing prerequisites; do not silently create a publicly readable bucket.
- Validate positive integer bounds, multipart provider constraints, maximum part count, expanded/compressed relation, retention, and deadline relationships. Use server limits in descriptors and enforce local stricter limits too.
- Capture timeout is the bounded commit/pause phase, not the total export/upload time. URL refresh cannot extend the operation's absolute upload deadline.
- Staging retention controls temporary artifacts, not published revision retention. Do not delete a referenced saved image after one day.
- If a required dependency is unavailable, fail closed for new save admission with a useful reason while keeping already accepted publication work reconcilable.

### 13.3 Worker configuration

Update `Worker/.env.example`, `Worker/setup_worker.md`, and the installation/service documentation. The service normally reads `/etc/dml/worker.env`, not Scheduler `.env.runtime`.

```dotenv
# Existing settings.
INTERACTIVE_WORKER_ENABLED=1
INTERACTIVE_ALLOW_INTERNET=1
INTERACTIVE_MAX_DURATION_SECONDS=7200
WORKER_STATE_DIR=/var/lib/dml-worker

# NEW settings to implement for this workflow.
WORKSPACE_SNAPSHOT_ENABLED=1
WORKSPACE_SNAPSHOT_SPOOL_DIR=/var/lib/dml-worker/snapshots
WORKSPACE_SNAPSHOT_MAX_COMPRESSED_BYTES=17179869184
WORKSPACE_SNAPSHOT_MAX_EXPANDED_BYTES=68719476736
WORKSPACE_SNAPSHOT_MIN_FREE_BYTES=85899345920
WORKSPACE_SNAPSHOT_S3_CA_FILE=
```

Keep the existing `SCHEDULER_URL`, Worker credential/CA, registry allowlist/pull credential, and pinned `INTERACTIVE_ACCESS_IMAGE`/preflight image settings. Scheduler and Worker internet gates must both allow package downloads.

`WORKSPACE_SNAPSHOT_ENABLED` is an explicit local capability gate. Negotiate stricter effective size limits with Scheduler; do not advertise support merely because an env variable says `1`. `MIN_FREE_BYTES` is an example 80 GiB reserve for a 64 GiB expanded/16 GiB compressed ceiling; calculate actual headroom across Docker and spool filesystems, allow concurrency reservations, and avoid double counting a shared disk. Do not reserve a whole worst-case image per assignment without explaining its scheduling impact.

Use a persistent, protected spool directory with owner-only permissions. The Worker service has `PrivateTmp=true`; avoid relying on host-visible `/tmp` paths for Docker binds. Worker downloads/uploads need the configured snapshot CA, but the Worker never receives the Scheduler's S3 access/secret key or registry push credentials.

### 13.4 Builder configuration

Update `Docker_Image_Builder/.env` examples and Compose wiring. Reuse actual `SCHEDULER_API_URL`, `DOCKER_HUB_USERNAME`, existing registry push authentication, Builder ID, and the interactive shared secret mount.

```dotenv
# NEW Builder settings to implement.
WORKSPACE_SNAPSHOT_IMPORT_ENABLED=1
WORKSPACE_SNAPSHOT_SPOOL_DIR=/data/snapshots
WORKSPACE_SNAPSHOT_MAX_COMPRESSED_BYTES=17179869184
WORKSPACE_SNAPSHOT_MAX_EXPANDED_BYTES=68719476736
WORKSPACE_SNAPSHOT_MIN_FREE_BYTES=85899345920
WORKSPACE_SNAPSHOT_S3_CA_FILE=
```

Advertise/claim supported work explicitly so an older or disabled Builder cannot claim a SNAPSHOT revision or workspace-source batch derivation it cannot execute. The compatibility gate must cover both kinds. Validate registry repository privacy/access for saved user images; current use of a Docker Hub username alone does not prove repositories are private.

Retain `/data` persistence or mount a dedicated spool volume. Set ownership for the actual Builder process. Account for compressed spool, Docker load expansion, shared layers, and simultaneous builds. Do not increase concurrency beyond disk and memory budgets.

### 13.5 Storage and Compose wiring

- Provision the private versioned bucket and restricted service account through a documented admin/bootstrap command. Keep credentials out of command-line logs.
- Add non-secret reserved-bucket configuration to the generic Object Store service, for example `OBJECT_STORE_RESERVED_BUCKETS=workspace-snapshots`, and implement route rejection. Its general credentials should not grant access to the snapshot bucket.
- Mount the Scheduler credential file read-only through `compose.runtime.yaml`, using a host interpolation variable such as `SNAPSHOT_STORAGE_CREDENTIALS_HOST_FILE`. Mount the snapshot CA when configured, and mount the same CA on Workers/Builders needing it.
- Keep rollout with feature flags off possible before these new mounts/files exist. Use a deliberate optional snapshot overlay/profile or document/provision prerequisites first; do not accidentally break every existing deployment by adding an unconditional required mount.
- Extend `restart.sh` preflight and overlay selection to check exact required files when snapshot support is configured, without dumping expanded secrets. Reuse existing service/network volumes.
- A Compose service `environment:` value overrides the same key from service `env_file`. Existing `OBJECT_STORE_URL` is defined in base `Scheduler/docker-compose.yml`; simply adding it to `.env.runtime` may have no effect. Audit and test every overlapping variable.
- `${...}` host-path interpolation is supplied by shell/Compose `.env`/`--env-file`, not by a service's `env_file`. Document where each host interpolation variable belongs. Do not assume writing `SNAPSHOT_STORAGE_CREDENTIALS_HOST_FILE` into service `.env.runtime` supplies the bind source.
- Changing a service env file requires container recreation; plain `docker compose restart` does not recreate its environment. Worker systemd environment changes require service restart; avoid interrupting active unsaved sessions without a drain procedure.

Deliver a settings table in operator docs with name, existing/new status, consumer, units/default, whether pinned at runtime creation, and required restart. No orphan env setting should remain without code that reads and enforces it.

## 14. Tests and evidence required before activation

### 14.1 Unit and service tests

- Owner isolation for saves, revisions, operation lists, and submissions.
- Flag/capability disabled responses and frontend disabled reasons.
- Save/submission atomic admission, repeated identical requests, changed request conflicts, replay after runtime release, fresh second Save from the same runtime.
- Full Worker fence, lease expiry, stale instance/generation, capture token mismatch, Stop/capture races.
- Artifact missing from storage, checksum/size mismatch, multipart retries, accepted receipt replay, URL expiry/renewal, late overwrite/version rejection, and restricted generic Object Store routes.
- Real state progression to terminal status, failed save preserving prior head, retry exhaustion, and stale ready callback failing to advance the head.
- Training settings/priority authorization, explicit argv, image source discrimination, and no archive rebuild for snapshot-source jobs.
- Safe cancellation/cleanup and bounded disk/memory behavior. Tests must exercise outcomes; asserting a mocked `commit()` was called does not prove persistence.
- UI flush-before-save, conflict handling, successive save keys, settings form, reload recovery, timer cancellation, truthful queued-versus-running state.

### 14.2 PostgreSQL integration tests

Use actual PostgreSQL, not only the SQLite fixtures in `Scheduler/test/conftest.py`:

- Upgrade a database containing existing jobs/workspaces through migrations 001–004, and run startup migrations twice.
- Validate `BIGINT`, source-specific nullability, composite ownership keys, partial unique indexes, and immutable triggers.
- Two API processes admit simultaneous saves/submissions without duplicates.
- Two reconcilers create exactly one job and one revision under lost-response retries.
- A late publication cannot replace a newer saved head. Lock ordering does not deadlock under concurrent heartbeat, Save, Stop, and publication.

### 14.3 Real Docker + storage + registry integration

Add a disposable integration harness using production capture/import/controller code, actual PostgreSQL, a private versioned test bucket, and an isolated registry. Build a small CPU fixture with a local wheel so core persistence tests do not depend on public PyPI.

Required sequence:

1. Build an owned initial image with a training script that imports a package absent from the image.
2. Start an interactive workload through the real code, edit the script, and install the wheel as UID 10001 with the documented command.
3. Request Save through the owner API. Wait for `SUCCEEDED`, inspect the registry digest, and record artifact receipt/version.
4. Destroy the runtime normally, start a fresh runtime from that revision, and assert package import/version, interpreter, UID, HOME, code hash, executable PATH, and workdir.
5. Modify a second time and Save again in the same runtime. Verify a different revision captures the new changes, the previous revision remains readable, and retries do not create a third revision.
6. Submit with a non-default Python script/arguments; wait for runtime release and exactly one job.
7. Execute the produced image with the actual batch launch path and verify installed-package use and output files. CPU fixture can validate environment/output mechanics; it does not satisfy GPU estimation acceptance.
8. Confirm executable `Entrypoint`/`Cmd` do not retain the interactive sleep loop and source/workspace digests are unchanged.
9. Test a large synthetic artifact with measured bounded process RSS, streamed transfer, enforced limits, and cancellation.

Run tests with a real Docker SDK/daemon; mocks currently conceal invalid pause/unpause arguments and ineffective executable-image retagging. Add fault injection after commit, mid-export, mid-upload, after storage completion before response, after push before callback, and after job insert before response. Restart each relevant process and prove recovery or explicit terminal failure without misleading success.

### 14.4 Browser, GPU, and multiple-host acceptance

- A real browser performs create/select -> edit/install -> Save -> Stop -> Restart, and separately Submit -> linked job.
- Scheduler, storage, Builder, and Worker communicate using deployed routable origins and TLS; do not accept a same-process fake as proof of multi-host handoff.
- Capture on Worker A, then resume interactively and execute batch work on a clean compatible Worker B without using A's local image/spool cache.
- Use a pinned PyTorch/CUDA fixture with a real optimizer step so the existing VRAM estimator can produce its required report. Verify estimation, scheduling, GPU training, output upload, and checkpoint resume.
- Verify non-root report/output permissions, package imports, digest selection, and source/executable lineage in the real execution path.
- Test runtime expiry near capture, deliberate Worker loss, and browser reload mid-submission. The prior saved revision must remain usable.
- Save with internet enabled, then verify importing the installed fixture after restarting with internet disabled; persistence must not rely on reinstalling packages at launch.

### 14.5 Running and recording checks

Use the repository's component environments and existing CI conventions. Typical existing commands, from the named directories:

```text
Scheduler:            python -m pytest test/unit/ -q
Worker:               python -m pytest test/unit/ -q
Docker_Image_Builder: python -m pytest test/unit/ -q
Access_Container:      python -m pytest test/unit/ test/component/ -q
UI/User:              npm run test:interactive
UI/User:              npm run build
```

Add explicit scripts/CI jobs for new PostgreSQL, snapshot Docker/storage, browser, and GPU acceptance checks. Use a Node version compatible with the actual locked frontend dependencies. Inspect existing PostgreSQL/E2E harness requirements before invoking them; do not invent passing commands for scripts that do not exist yet.

Record commit, component versions, fixtures, commands, pass/fail/skip, and artifact evidence under an agreed ignored/local artifact directory and summarize results in docs. A skipped GPU or multi-host test is a remaining activation gate, not a pass. Do not claim deployment verification from static inspection or mocks.

## 15. Deployment, activation, and rollback

Deploy all components, not only Scheduler. Root `restart.sh` updates Scheduler and configured control-plane services; the remote Worker and Builder require their own documented update/restart steps. Rebuild/deploy the user UI too.

Order:

1. Back up database/configuration, record deployed versions, and stop new admission/drain affected sessions before changes that would destroy unsaved work. Do not run `docker compose down -v`, broad prune, or wipe queues.
2. Provision private snapshot storage, versioning, service policy, routable TLS endpoint, spool capacity, and protected credential/CA mounts.
3. Deploy Object Store access restrictions and new code/migrations with save/submission admission off. Ensure old archive/output flows continue working.
4. Deploy compatible Worker and Builder code with explicit capabilities; verify their flags, real Docker SDK calls, CA access, spool permissions, registry access, and heartbeat versions.
5. Deploy Scheduler controllers and UI capability handling. Accepted workflow reconciliation must keep running even if new admission flags are turned off.
6. Run disposable integration gates, then enable Save in staging and complete save/restart across hosts. Enable Submit in staging and complete GPU acceptance.
7. For the target deployment, set the accepted `.env.runtime` values and matching Worker/Builder configuration. Recreate Scheduler/Builder containers as appropriate and restart drained Workers. Start a new runtime to receive new pinned environment/network settings.
8. Verify effective non-secret configuration inside the actual services without printing credentials. Verify capabilities in API responses, not just the contents of a host file.
9. Perform one real user Save/Restart and one Submit workflow, record image digests/job ID, and check that the resulting job is visible in the normal dashboard.

Operator runbook must include exact commands using the actual deployment manifests/project names, prerequisite file checks, health endpoints, and remote host responsibilities. Keep deployment-specific real secrets out of the repository.

Rollback:

- First disable new Save and Submit admission; existing accepted operations continue reconciliation or reach an explicit safe terminal state.
- Disabling internet affects newly created runtimes; it does not retroactively change a live container network.
- Preserve published images, accepted artifacts, database provenance, and assignment tombstones. Keep additive schema when rolling application code back.
- An old Worker/Builder must not claim jobs or saves requiring unsupported source/protocol fields. Drain such work or keep compatible processors running until completion.
- Keep a repair procedure for exact stuck operation/upload/image identities. Never advise broad bucket deletion, volume deletion, or image pruning as a recovery step.

## 16. Implementation sequence and final completion checklist

Work in reviewable changes with meaningful tests after each phase:

1. [ ] Baseline inspection, contracts, and executable failing regression cases.
2. [ ] Migration/ORM alignment, atomic admission/idempotency, capability APIs.
3. [ ] Portable non-root Python environment and batch/estimator identity compatibility.
4. [ ] Fenced command dispatch, real capture, bounded spool, health/cleanup recovery.
5. [ ] Private multipart storage, versioned receipts, Builder download capabilities.
6. [ ] Streaming import/publication, atomic save completion, safe saved-head updates.
7. [ ] Submission reconciler, runtime release gating, exactly one snapshot-source job.
8. [ ] Executable child build, digest-based estimation/training/resume.
9. [ ] Complete UI settings/status/recovery/history and truthful capability gating.
10. [ ] Environment parsers, example files, Compose mounts, restart/install runbooks.
11. [ ] PostgreSQL and real Docker/storage integration passing, fault recovery verified.
12. [ ] Real browser, GPU, and independent-host acceptance with recorded evidence.
13. [ ] Target deployment configuration verified and user workflow exercised, if deployment is part of the implementing agent's authorized task and infrastructure is available.

Final handoff must state implemented behavior, actual validation results, exact configuration changes per host, migration/deployment status, and any remaining activation gate. If infrastructure is unavailable, finish the implementation and runnable harness/runbook, clearly identify the unrun gate, and keep production activation off. Do not replace missing evidence with a claim that the feature is production-ready.

Update `features.md`, `docs/interactive-runtime-operations.md`, environment examples, and relevant READMEs so statements about save availability match the finished code. Mark obsolete architecture documentation as historical where it could mislead the next maintainer.

## 17. Technical references checked for this plan

- [Docker commit documentation](https://docs.docker.com/reference/cli/docker/container/commit/): commits create a new image, pause by default, and exclude mounted-volume contents. This plan consequently keeps persisted workspace/package files in the workload filesystem and treats process state separately.
- [Docker SDK container API](https://docker-py.readthedocs.io/en/stable/containers.html): verify the deployed version's `commit`, `pause`, and `unpause` signatures and error handling rather than relying on permissive mocks.
- [Compose environment precedence](https://docs.docker.com/compose/how-tos/environment-variables/envvars-precedence/): explicit service environment entries take precedence over service env files. Validate effective runtime configuration after overlay merging.

These references establish Docker/Compose behavior; the implementation and deployment details above are requirements for this repository, not claims that the corresponding functionality already exists.
