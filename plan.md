# Unified interactive workspace and machine-capacity flow

Status: implementation handoff. This document is a plan, not a claim that the feature is implemented.

Prepared against the repository on 2026-09-24. Reinspect the checkout before editing and preserve unrelated work.

## 1. Required outcome

Replace the separate **Machines** and **Interactive workspaces** experiences with one end-to-end interactive-access flow:

1. The user creates or chooses an interactive workspace image.
2. For a new uploaded workspace, the user chooses the PyTorch/CUDA base image. For a workspace derived from an existing job, show the already-pinned source image rather than pretending it can be changed.
3. The user selects minimum system requirements: GPU model (optional), GPU VRAM, CPU cores, RAM, and writable disk.
4. As requirements change, show fresh, online workers whose hardware can satisfy them using the existing Machines-page card presentation. Each card must show current load and whether its current work is batch training, VRAM estimation, or interactive access.
5. The user requests interactive access without selecting a worker. The request contains requirements only; it must never contain a `worker_id`, hostname, IP, or GPU UUID.
6. The Scheduler queues an `InteractiveRuntime` and assigns any currently eligible worker whose capabilities are greater than or equal to all numeric requirements. GPU model is an optional exact categorical constraint; an omitted model means any model with enough VRAM.
7. The user receives an in-app notification when a worker is assigned and another when the runtime is ready. The existing details page continues to show all intermediate states while polling.
8. Once ready, the user opens the existing workspace editor/terminal on that assigned runtime.

The final flow must preserve image building, runtime fencing, exclusive interactive reservations, leases, cleanup, stop, save, training submission, access grants, and editor behavior.

## 2. Repository findings that determine the design

### 2.1 Use the runtime queue, not `resource_requests`

`UI/User/src/pages/Machines.tsx` currently sends `POST /resources/request`. That endpoint only inserts a `ResourceRequest(status="PENDING")`; no scheduler code consumes or closes those rows and no worker can be assigned through it.

The real interactive scheduler already exists:

```text
POST /interactive/workspaces/{workspace_id}/runtimes
  -> InteractiveRuntime(state=QUEUED, immutable launch_spec)
  -> scheduling.policy chooses a compatible worker
  -> WorkerAssignment(kind=interactive_access, exclusive=true)
  -> ASSIGNED -> PULLING -> STARTING -> CONNECTING -> READY
  -> existing workspace connection/editor
```

Extend this path. Do not add a second assignment mechanism and do not try to join `resource_requests` to runtimes.

### 2.2 Current interactive requirements are operator-fixed

`Scheduler/app/services/scheduling/config.py::resource_profile()` currently reads one profile from environment variables. `interactive_runtime_service.start()` pins that profile into `InteractiveRuntime.launch_spec`, and `scheduling.policy.compatible_gpu()` uses it for placement. The Worker already applies the pinned CPU, RAM, disk, PID and GPU settings when creating the workload container.

The implementation therefore needs to safely construct a launch spec from operator policy plus user-selected minimum resources. It does not need a new Worker launch protocol.

### 2.3 Current machine workload counts are too coarse

`worker_service.get_all_workers()` returns only `running_jobs`, calculated from every live `WorkerAssignment`. It does not distinguish batch, estimation, and interactive work. The merged view needs a sanitized workload summary derived from `WorkerAssignment.kind` and state.

### 2.4 Interactive placement is exclusive today

The policy only places interactive work on a worker with no scheduler or worker-local assignments, and the assignment has `exclusive=true`. Preserve this. A capable worker running batch work may appear in the capacity preview as busy, but it is not `available_now` and cannot receive the interactive runtime until it is empty.

## 3. Product assumptions for this implementation

Use these defaults unless the product owner explicitly changes them before implementation:

- “Notify” means an accessible in-app toast/status notification while the web app is open. Email, SMS, OS push, and notifications after the browser is closed are out of scope because the repository has no delivery infrastructure for them.
- Show online, interactive-capable machines that satisfy the hardware requirements, including busy matches. Mark each card **Available now**, **Busy**, or **Unavailable for interactive access** and explain the current work type. Do not show offline workers in this interactive picker.
- Keep worker selection automatic. Machine cards are informational and have no select/radio control.
- Treat all numeric choices as minimums (`>=`). Remove the Machines page's `=` comparison option from this flow.
- Treat GPU model as an exact label only because models do not have a reliable total ordering. “Any GPU” plus minimum VRAM is the general “equal or better” option.
- New-upload workspaces retain the current PyTorch/CUDA base-image picker. Existing-job workspaces retain their source image and display it read-only.
- Requirements saved during workspace creation are defaults for later runtime requests. The user may revise them immediately before each new runtime, and the runtime pins the final submitted copy.

## 4. Target user experience

### 4.1 Navigation and routes

- Keep `/interactive` as the single navigation entry, labelled **Interactive workspaces** (or **Interactive access** if copy is being refreshed).
- Remove the separate **Machines** navigation item from `UI/User/src/components/Layout.tsx`.
- Replace the `/machines` page route with `<Navigate to="/interactive" replace />` so saved links do not break.
- Retire `UI/User/src/pages/Machines.tsx` after extracting its reusable requirement controls and machine card presentation.
- Keep `/interactive/:id` for build/runtime state and requesting access, and `/interactive/:id/editor` for the existing full-screen IDE.
- Make `/submit?mode=interactive` redirect to or render the same unified create flow rather than maintaining a second implementation.

### 4.2 Unified workspace page

`InteractiveWorkspaces.tsx` should become the hub, with:

- a **New interactive workspace** action/form using the existing `InteractiveCreate` fields;
- the base-image selector for upload/empty sources;
- a **My workspaces** list showing image state and latest runtime state;
- active queued/assigned/ready runtime callouts;
- no standalone generic “Acquire machine” queue.

For a new workspace, place the requirements and capacity preview after the source/base-image fields. The create request stores these requirements as workspace defaults. Image creation and runtime admission remain distinct durable actions:

```text
Create workspace image -> build reaches IMAGE_READY -> Request interactive access
```

Do not create a runtime before a revision is `IMAGE_READY`. While a build is pending, keep showing the chosen defaults and matching capacity, but disable the access button with “Workspace image is still building.”

### 4.3 Workspace details and runtime request

Replace the generic **Start** button in `InteractiveDetails.tsx` with a clear **Request interactive access** section:

- base/source image summary;
- editable minimum requirements, initially taken from the latest runtime if present, otherwise the workspace defaults;
- debounced live capacity preview;
- matching machine cards in the extracted Machines-page format;
- summary counts: matching online, available now, busy, and queued interactive requests;
- a button that sends the requirements to the runtime start endpoint.

After submission:

- show `QUEUED` as “Waiting for a matching machine”;
- show `ASSIGNED`, `PULLING`, `STARTING`, and `CONNECTING` as preparation states;
- when assigned, show a sanitized assigned-machine summary but no control to change it;
- when `READY`, emphasize **Open Editor** and retain Stop;
- on `FAILED` or `LOST`, show the existing failure detail and allow a new request after cleanup.

The existing two-second detail polling can drive these transitions. Pause capacity-preview polling once a runtime is live; the assigned runtime state is then authoritative.

## 5. API and persistence contracts

### 5.1 Canonical requirements object

Add one strict Pydantic model and use it for workspace defaults, preview, and runtime start:

```json
{
  "gpu_model": "NVIDIA A100-SXM4-40GB",
  "minimum_vram_gb": 16,
  "cpu_cores": 4,
  "memory_gb": 16,
  "disk_gb": 50
}
```

Rules:

- `gpu_model` is nullable/optional and bounded in length.
- Numeric fields are finite and positive and have conservative absolute maxima.
- Unknown fields are rejected.
- The request has no placement identity field. A test must prove that `worker_id`, hostname, GPU UUID, or similar extras return 422.
- The server validates against operator-configured minimum/maximum limits; do not trust dropdown values as authorization.
- Canonicalize floats/numbers before hashing idempotent requests so `4` and `4.0` do not create inconsistent hashes.

Keep security and operational launch fields server-owned: platform, PID limit, root policy, internet policy, pull headroom, service images, access protocol, and lifetime limits must never come from the browser.

### 5.2 Operator bounds and launch-spec construction

Refactor `resource_profile()` into a function that returns operator policy/defaults and a separate builder that accepts validated requirements. Add documented environment bounds, for example:

```text
INTERACTIVE_MIN_CPU / INTERACTIVE_MAX_CPU
INTERACTIVE_MIN_RAM_GB / INTERACTIVE_MAX_RAM_GB
INTERACTIVE_MIN_WRITABLE_GB / INTERACTIVE_MAX_WRITABLE_GB
INTERACTIVE_MIN_VRAM_GB / INTERACTIVE_MAX_VRAM_GB
INTERACTIVE_GPU_MODELS                 optional operator allowlist
```

Retain existing `INTERACTIVE_CPU`, `INTERACTIVE_RAM_GB`, `INTERACTIVE_WRITABLE_GB`, and `INTERACTIVE_MIN_VRAM_GB` as defaults for backward compatibility. Update `Scheduler/.env.runtime.example`, deployment examples, and config tests.

The resulting immutable `launch_spec` should contain the requested CPU/RAM/disk limits, minimum VRAM, zero-or-one requested GPU model allowlist, and all existing server-owned fields. `profile_version` remains operator-owned. Include the canonical requirements in the idempotency hash for `start()`.

### 5.3 Workspace defaults

Add an additive, rerunnable PostgreSQL migration after `006_package_only_legacy_check.sql` and update `InteractiveWorkspace`:

```text
default_resource_requirements JSON nullable
```

Existing workspaces may remain null and fall back to current operator defaults. New upload and from-job creation requests accept the canonical requirements and persist them. Include the requirements in workspace creation's idempotency hash.

For multipart upload creation, carry requirements as a bounded JSON form field and parse it through the strict Pydantic model. For from-job creation, use a nested JSON object. Do not accept individual unvalidated free-form launch arguments.

Expose defaults and source/base-image metadata from `interactive_workspace_service.public()`:

```json
{
  "default_resource_requirements": { "...": "..." },
  "revision": {
    "requested_base_image": "pytorch/pytorch:...",
    "source_image_tag": "..."
  }
}
```

Only expose the fields the user needs. Do not expose registry credentials, builder leases, or internal image metadata.

### 5.4 Runtime start and public runtime response

Extend `Start` in `Scheduler/app/schemas/worker_execution_schema.py`:

```json
{
  "revision_id": null,
  "requirements": { "...": "..." }
}
```

`requirements` may be omitted for backward compatibility, in which case use the workspace default and then operator defaults. `interactive_runtime_service.start()` must:

1. validate ownership and ready revision as today;
2. build a safe launch spec from the requirements and operator policy;
3. include the canonical requirements in the request hash;
4. pin the spec into the runtime before it enters `QUEUED`;
5. preserve current one-live-runtime-per-workspace and idempotency behavior.

Extend the public runtime response with a sanitized `requirements` object and, after assignment, an `assigned_machine` object such as display name, GPU model, total VRAM, and assignment time. Never expose attempt tokens, worker instance IDs, private IPs, assignment payloads, or management resource IDs.

No new runtime column is required: `launch_spec` is already immutable and is the correct scheduling source of truth.

### 5.5 Capacity endpoints

Create an authenticated interactive-capacity API rather than expanding the unauthenticated legacy worker dashboard contract:

```text
GET  /interactive/capacity/options
POST /interactive/capacity/preview
```

Suggested files:

- `Scheduler/app/schemas/interactive_capacity_schema.py`
- `Scheduler/app/services/interactive_capacity_service.py`
- `Scheduler/app/api/interactive_capacity_route.py`
- register the router in `Scheduler/app/main.py`

`options` returns server defaults/bounds plus distinct currently supportable GPU models and safe numeric choices. Derive choices from fresh authenticated Worker v1 inventory, intersected with operator bounds. Do not offer raw host totals that cannot satisfy scheduler headroom. An empty cluster still returns operator defaults and bounds so the form remains usable.

`preview` accepts only the canonical requirements and returns:

```json
{
  "generated_at": "...",
  "requirements": { "...": "..." },
  "matching_online": 2,
  "available_now": 1,
  "busy": 1,
  "queued_interactive_requests": 3,
  "machines": [
    {
      "machine_key": "opaque-display-key",
      "display_name": "gpu-worker-02",
      "gpu_model": "NVIDIA A100-SXM4-40GB",
      "gpu_count": 2,
      "total_vram_gb": 40,
      "free_vram_gb": 38,
      "cpu_cores": 32,
      "cpu_load_percent": 22,
      "total_ram_gb": 128,
      "free_ram_gb": 96,
      "total_disk_gb": 1000,
      "free_disk_gb": 600,
      "gpu_load_percent": 10,
      "available_now": true,
      "availability_reason": null,
      "workloads": [
        { "kind": "batch_training", "state": "ACTIVE", "mine": false }
      ]
    }
  ]
}
```

Privacy and correctness requirements:

- Include only workers with a fresh authenticated heartbeat and complete inventory.
- Include only machines with compatible platform/runtime/quota support and total hardware capability at or above the request.
- Determine `available_now` using current free capacity, GPU process/busy state, worker mode, pause/drain/reconcile state, scheduler assignments, worker-local assignments, and headroom.
- Return workload kind/state only. For another user's work, never return job/workspace name, user identity, command, image, payload, or IDs. `mine` can allow a “Your batch training” label without leaking other users.
- Machine keys are display/diff keys only. The runtime-start API must not accept them.
- Mark the response `Cache-Control: no-store`; it is a point-in-time preview, not a reservation.
- Do not promise that `available_now` remains free. The durable claim transaction is the only assignment authority.

## 6. Scheduler eligibility and placement

### 6.1 Share one matcher

Refactor `Scheduler/app/services/scheduling/policy.py` so preview and placement cannot drift. Separate:

- **capability match**: platform, Nvidia runtime, disk quota support, total CPU/RAM/disk, and at least one acceptable GPU with enough total VRAM;
- **availability match**: fresh inventory, no conflicting assignments or local work, worker mode available, free RAM/disk/VRAM with headroom, idle selected GPU, and no pause/drain/reconcile condition.

Keep `interactive_ineligibility()` as the stable diagnostic layer or split it into capability and availability reason functions. Both the capacity service and `compatible_gpu()` must call these shared functions.

### 6.2 Equal-or-better semantics

For every numeric field, accept a worker only when its relevant allocatable capacity is `>=` the requested value. Preserve existing safety overhead:

- CPU: host capacity and current availability must cover requested cores plus the scheduler's reserved headroom where applicable.
- RAM: current free RAM must cover `memory_gb + 1` as it does today.
- Disk: current free disk must cover `disk_gb + pull_headroom_gb`.
- GPU: one individual idle GPU must have `memory_gb >= minimum_vram_gb`; do not sum VRAM across GPUs.
- GPU model: when supplied, that individual GPU's model must match; when omitted, any model is valid.

Do not match on `Worker.gpu_type` alone when per-GPU authenticated inventory exists. The chosen GPU UUID stays an internal claim result and is never user-controlled.

### 6.3 Queue and claim behavior

Keep the current queue ordering unless product policy is separately changed: estimation, interactive when the worker is empty, then batch/retry. Within interactive requests, retain `created_at, id` FIFO traversal and skip incompatible requests so one oversized request does not block smaller ones.

At claim time, re-run all checks while holding the existing worker/runtime locks. A stale capacity preview must not weaken admission. Preserve:

- `WorkerAssignment(kind='interactive_access', exclusive=true)`;
- one live interactive assignment per worker;
- one live runtime per workspace;
- assignment/runtime foreign keys and immutability triggers;
- lease, event fencing, cleanup, and management revocation behavior.

## 7. Machine workload visibility

Add a helper in `worker_service` or the new capacity service that loads live `WorkerAssignment` rows in one batched query for all candidate workers; avoid an N+1 query per card.

Map assignment kinds for the UI:

- `batch_training` -> **Batch training**
- `vram_estimation` -> **VRAM estimation**
- `interactive_access` -> **Interactive workspace**

Include claim/active/cleaning state so a user can understand why a machine is not available. Count unreleased cleaning/lost reservations as occupied until normal cleanup releases them. If worker-local inventory reports an assignment not represented in the Scheduler, show a generic **Worker-local/reconciling work** indicator and mark the worker unavailable rather than claiming it is idle.

Fix presentation issues while extracting the card:

- CPU load bar uses `cpu_load`, not `mem_usage` (the current Machines page uses the wrong value).
- RAM displays free/total where inventory supports it, with percent derived consistently.
- Disk displays writable free capacity, not a misleading exact future guarantee.
- Interactive cards do not show offline styling because offline workers are omitted.

The existing `/workers/nodes` endpoint may continue serving admin/legacy views, but the User interactive page must use the authenticated capacity contract. If `running_jobs` remains in the old response, document that it is only a total and not the new workload source.

## 8. User UI implementation

### 8.1 Shared components and services

Extract from `Machines.tsx` into `UI/User/src/features/interactive-capacity/` (names may vary):

- `ResourceRequirementsForm.tsx`
- `CapacitySummary.tsx`
- `MachineGrid.tsx`
- `MachineCard.tsx`
- `useCapacityPreview.ts`
- shared types/normalizers

Move the relevant CSS from page-specific assumptions into reusable classes in `UI/User/src/index.css`. Preserve responsive behavior and accessible labels/status text.

Add typed methods in `UI/User/src/services/interactive.ts` or a focused `interactiveCapacity.ts`:

- load options;
- preview requirements with `AbortController` support;
- create workspace including defaults;
- start runtime including requirements;
- list the user's latest/active runtime states for notifications.

Delete `createResourceRequest()` usage from the User UI. `UI/User/src/services/workers.ts` may retain legacy node functions only if another screen uses them; otherwise remove dead User-side resource request types.

### 8.2 Capacity-preview behavior

- Debounce requirement changes by roughly 250–400 ms.
- Cancel the previous preview request when inputs change.
- Ignore stale responses using request identity/abort signals.
- Show loading, empty-cluster, no-match, and API-error states independently from workspace-build errors.
- Do not optimistically remove a machine when access is requested; switch to the runtime status returned by the server.
- Disable **Request interactive access** if the revision is not ready, a live runtime already exists, requirements are invalid, or no matching online machine exists. If matches exist but all are busy, allow queueing and label the button **Queue interactive access**.
- When no capable online machine exists, explain that requirements can be reduced or the user can wait for a suitable worker to come online; do not submit an unfulfillable request accidentally.

### 8.3 Base-image behavior

Retain `fetchPytorchVersions()` and the PyTorch/CUDA selectors for upload/empty workspaces. The selected full runtime tag is the base image ID sent to the existing allowlisted resolver.

For `source='job'`:

- extend the owned source-job choice response with a safe base/source-image label;
- show **Base image inherited from existing job**;
- do not present an editable base-image dropdown because changing it would no longer describe the built source image.

On workspace details, display `revision.requested_base_image` for upload origins or the source-image label for existing-job origins.

### 8.4 Notifications

Add a small authenticated runtime watcher mounted inside `Layout` (but not duplicated inside the full-screen editor). It should poll a lightweight owned-runtime endpoint at a slower interval, such as five seconds, while the document is visible.

Track the last seen `(runtime_id, generation, state)` and emit accessible toasts for transitions:

- first observation of `ASSIGNED`: “A matching machine has been assigned to <workspace>.”
- first observation of `READY`: “<workspace> is ready.” with **Open Editor** action.
- transition to `FAILED` or `LOST`: concise failure notification with a link to details.

Use an `aria-live` toast region and ensure notifications are not duplicated on every poll or after reload. Store a bounded last-seen map in `sessionStorage` or local storage, namespaced by authenticated user, and clear it on logout. Do not request browser Notification permission as part of this change. The details page's inline status remains the source of truth if polling or storage fails.

## 9. Backend file-by-file work

Expected Scheduler edits:

- `Scheduler/app/schemas/interactive_capacity_schema.py` — strict requirements, options, preview, machine/workload responses.
- `Scheduler/app/services/scheduling/config.py` — operator defaults/bounds and safe launch-spec builder.
- `Scheduler/app/services/scheduling/policy.py` — shared capability/availability matching.
- `Scheduler/app/services/interactive_capacity_service.py` — fresh worker snapshots, batched assignments, sanitized preview.
- `Scheduler/app/api/interactive_capacity_route.py` — authenticated options/preview endpoints.
- `Scheduler/app/schemas/worker_execution_schema.py` — optional requirements on `Start`.
- `Scheduler/app/models/interactive_workspace_model.py` — persisted default requirements.
- `Scheduler/app/services/interactive_workspace_service.py` — create/hash/public defaults and base metadata.
- `Scheduler/app/api/interactive_workspace_route.py` — parse upload/from-job defaults safely.
- `Scheduler/app/services/interactive_runtime_service.py` — pin requested spec, expose requirements/assigned machine, owner runtime listing.
- `Scheduler/app/api/interactive_runtime_route.py` — active/latest runtime listing used by notifications.
- `Scheduler/app/services/worker_service.py` — only shared workload-query/serialization changes that are not kept in capacity service.
- `Scheduler/app/main.py` — register capacity router.
- `Scheduler/migrations/007_interactive_resource_defaults.sql` — additive workspace JSON column.
- `Scheduler/.env.runtime.example` and deployment documentation — new bounds and semantics.

Expected User UI edits:

- `UI/User/src/pages/InteractiveCreate.tsx`
- `UI/User/src/pages/InteractiveWorkspaces.tsx`
- `UI/User/src/pages/InteractiveDetails.tsx`
- `UI/User/src/pages/Machines.tsx` (extract then remove)
- `UI/User/src/components/Layout.tsx`
- `UI/User/src/App.tsx`
- `UI/User/src/services/interactive.ts`
- `UI/User/src/services/workers.ts` (remove obsolete interactive use)
- `UI/User/src/index.css`
- new shared capacity and notification components/hooks under `src/features/`

No Worker implementation change should be necessary because `Worker/interactive/docker_ops.py` already enforces the pinned `launch_spec` for CPU, memory, writable disk, GPU UUID, platform, root, and network. Add/adjust Worker tests if the shape of the launch spec changes to prove the existing enforcement still receives all required server-owned fields.

## 10. Tests

### 10.1 Scheduler schema/config tests

Extend `test_schemas.py` and `test_scheduling_config.py`:

- valid minimum requirements parse and canonicalize;
- zero, negative, non-finite, excessive, and extra fields fail;
- a submitted worker ID/GPU UUID fails;
- operator defaults work for old clients/workspaces;
- user values outside operator bounds fail without creating a runtime;
- user input cannot change platform, PID, root, internet, or headroom policy;
- workspace creation idempotency includes defaults;
- runtime start idempotency includes final requirements.

### 10.2 Capacity API/service tests

Add focused tests for:

- authentication and user-data redaction;
- only fresh authenticated complete workers are listed;
- numeric greater-than-or-equal matching at exact boundary and above it;
- one large GPU matches while multiple smaller GPUs do not sum together;
- optional GPU model behavior;
- total capability match versus current busy/free availability;
- pause, drain, reconcile, stale inventory, local assignments, quota/runtime failure, and GPU processes mark unavailable;
- batch, estimation, and interactive assignment labels and states;
- other users' names/IDs/payloads never appear;
- assignment lookup is batched;
- queued request counts only live `QUEUED` interactive runtimes;
- no workers still returns valid options/defaults;
- response is `no-store`.

### 10.3 Scheduling/runtime tests

Extend `Scheduler/test/unit/test_runtime_scheduling.py` and interactive route/service tests:

- start pins selected requirements in `launch_spec` and public response;
- a worker exactly at the allocatable boundary is eligible;
- a higher-capability worker is eligible;
- a lower CPU, RAM, disk, or per-GPU VRAM worker is skipped;
- requested GPU model is respected;
- the user cannot target an exact worker;
- among multiple eligible workers, whichever safely claims first receives the runtime;
- busy matching workers do not receive interactive work until all assignments cleanly release;
- an incompatible older queued runtime does not head-of-line block another request;
- stale preview data cannot bypass claim-time validation;
- assignment remains exclusive and all lease/fence/cleanup tests continue to pass;
- assigned-machine response is owner-scoped and sanitized;
- existing clients that send `{}` still use operator/default requirements.

### 10.4 Migration tests

Extend the PostgreSQL migration checks:

- migration 007 is rerunnable;
- an existing workspace remains valid with null defaults;
- a newly created workspace persists JSON defaults;
- the migration does not change runtime/assignment constraints or triggers.

### 10.5 User UI tests

Add Vitest/Testing Library coverage for:

- upload source shows editable PyTorch/CUDA base selection;
- existing-job source shows inherited base image and no editable selector;
- requirement changes debounce/cancel preview calls;
- only matching online machine cards render and busy cards show workload type;
- machine cards have no select control;
- exact-match and higher-capability cards are described consistently;
- image-building state disables access request;
- all-busy matches allow queueing, while zero capable matches prevent accidental submission;
- access request sends requirements but no machine identity;
- queued/assigned/ready/failure states render correctly;
- ASSIGNED and READY notifications fire once and READY links to the editor;
- `/machines` redirects to `/interactive` and the sidebar has only the merged entry;
- existing editor connection/stop behavior is unchanged.

### 10.6 End-to-end scenarios

Extend `test/interactive_e2e` where feasible:

1. Register two fresh workers: one under the request and one over it. Verify only the larger worker can claim.
2. Register two eligible workers and verify the request contains no target identity and either may claim safely.
3. Keep an eligible worker occupied by batch training; verify preview shows Batch training/Busy and the runtime remains queued.
4. Release/clean the batch assignment; verify the runtime becomes assigned and reaches ready.
5. Confirm the user notification state and open the existing editor/terminal.
6. Stop and clean the runtime, request a different profile, and confirm a new immutable generation is scheduled correctly.

## 11. Verification sequence

Run narrow tests while implementing, then full suites:

```bash
cd Scheduler
pytest -q test/unit/test_scheduling_config.py test/unit/test_schemas.py
pytest -q test/unit/test_runtime_scheduling.py test/unit/test_worker_service.py test/unit/test_api_routes.py

cd ../UI/User
npm run lint
npm run test:interactive
npm run build

cd ../../Worker
pytest -q test/unit/test_runtime_docker.py test/unit/test_execution_coordinator.py
```

Then run the full Scheduler, Worker, User UI, and interactive E2E suites supported by the environment.

Manual acceptance in a disposable multi-worker environment:

1. Open `/interactive`; verify there is no separate Machines navigation item.
2. Create an upload workspace and select a base image and minimum requirements.
3. Verify matching online machine cards update and show current resource load/work kind.
4. Confirm cards cannot be selected and the access request contains no worker identity.
5. Request access while all matches are busy; verify queued status.
6. Free one equal-or-better worker; verify assignment notification and assigned-machine summary.
7. Wait for ready; verify the ready notification and open the existing IDE/terminal.
8. Stop, verify cleanup, then request a different resource profile and ensure a new generation is placed accordingly.
9. Verify another user cannot see workspace/job names or identifiers in machine workload summaries.
10. Verify old `{}` runtime-start clients still receive the configured default profile.

## 12. Rollout and compatibility

Deploy in this order:

1. PostgreSQL migration and Scheduler support for optional workspace defaults, capacity preview, and requirement-aware runtime starts.
2. User UI merged flow and notifications.
3. Remove/deprecate legacy User-side Machines/resource-request code after observing the new flow.

The Scheduler change is backward compatible because omitted requirements use defaults. The Worker receives the same launch-spec keys it already understands. Existing runtimes keep their already-pinned specs.

Do not drop `resource_requests` or its endpoints in this feature rollout. Stop creating new rows from the User UI and mark the path deprecated; database removal can be a separate cleanup after confirming no external client uses it. Keep `/machines` as a redirect for at least one release.

Log and monitor:

- capacity-preview failures/latency without logging user tokens or full assignment payloads;
- runtime requests by normalized requirement bucket;
- queue-to-assignment and assignment-to-ready latency;
- no-capable-worker versus capable-but-busy counts;
- placement rejection reasons at claim time;
- runtime failures and cleanup duration;
- notification polling failures.

Rollback the UI first if needed. Already-created runtimes remain safe because requirements are pinned in their launch specs and the old worker execution path understands those fields.

## 13. Definition of done

- Machines and Interactive workspaces are one user-facing flow with one navigation entry.
- A new interactive upload exposes a real base-image selector; derived workspaces clearly show their inherited image.
- Users can choose bounded minimum GPU/VRAM/CPU/RAM/disk requirements.
- The UI shows only fresh online capable machines in the existing card style, including current load and batch/estimation/interactive occupancy.
- No machine card is selectable and no runtime request accepts a machine identity.
- The real `InteractiveRuntime` queue, not `resource_requests`, performs assignment.
- Placement accepts equal-or-better capacity, revalidates at claim time, and preserves exclusive reservations and fencing.
- The user is notified once on assignment and once on readiness, then can use the existing editor/terminal.
- Cross-user workload details are not leaked.
- Legacy default-profile starts, workspace builds, stop/cleanup, save, training submission, and editor access have no regressions.
- Unit, migration, UI, and available E2E tests pass, with results recorded in the implementation handoff.
