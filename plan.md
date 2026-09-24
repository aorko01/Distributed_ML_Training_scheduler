# Package-only workspace images (no ZIP): implementation plan

Status: implementation handoff. This document describes the work; it does not claim the feature is implemented.

Prepared against the repository on 2026-09-24. Reinspect the checkout before editing and preserve unrelated changes.

## 1. Required outcome

The **Workspace (batch job)** form must allow submission without a ZIP archive.

When the user submits without a ZIP:

1. Show an accessible confirmation pop-up before making the request.
2. Explain that `/workspace` will contain no uploaded files, selected Python packages will still be installed, and an empty package list produces a base-image-only environment.
3. Explain that the resulting image is **interactive-only** and cannot be submitted directly for training.
4. On confirmation, build and publish the image from the selected base image plus the package list.
5. Keep the image eligible as a source for an interactive workspace, where the user can add files inside the running container/editor.
6. Enforce the training prohibition in the Scheduler, not only by hiding UI controls.

ZIP-backed workspaces must keep their current build and training behavior. Existing interactive uploads, existing-job interactive workspaces, build leases, retries, logs, cancellation, and registry publication must continue to work.

## 2. Important scope correction

Most visible changes are in `UI/User/` and the package-only build branch is in `Docker_Image_Builder/`, but those directories are not sufficient by themselves:

- `Scheduler/app/api/jobs_route.py` currently declares `zip_file: UploadFile = File(...)`.
- `Scheduler/app/models/job_model.py` currently makes `object_key` non-nullable.
- `Docker_Image_Builder/builder.py` currently treats a missing `object_key` as a malformed claim.
- `Scheduler/app/services/job_service.py::submit_training` currently permits every owned `IMAGE_READY` job with an image tag.

Therefore the implementation must include the narrow Scheduler model/API/service/migration changes below. Do not ship a UI-only training restriction; a direct API call would bypass it.

No Object Store change is required. A package-only submission must skip upload entirely. No Worker change should be required because these images do not enter estimation or training directly.

## 3. Data and API contract

### 3.1 Use explicit provenance

Use the existing `jobs.source_kind` database concept from migration `003_workspace_editor_snapshots.sql`, and map it in the SQLAlchemy `Job` model. Support these relevant values:

- `ARCHIVE`: current ZIP-backed job; directly trainable after its image is ready.
- `PACKAGES_ONLY`: no ZIP/object; interactive-only and never directly trainable.
- Preserve `WORKSPACE_REVISION` for the separate durable-workspace flow; do not conflate it with `PACKAGES_ONLY` or regress its eventual training path.

All existing rows remain `ARCHIVE` through the existing default. The server, not a client-supplied flag, determines `source_kind` from whether a ZIP part is present.

Expose both `source_kind` and a derived `training_eligible` boolean in user-facing job responses. `training_eligible` is false for `PACKAGES_ONLY`; keeping this derived value in responses gives the UI a simple capability while `source_kind` preserves provenance and debuggability.

### 3.2 Make the archive truly optional

Add a new idempotent migration, following the numbered migration convention, which:

- makes `jobs.object_key` nullable;
- ensures legacy rows retain `source_kind = 'ARCHIVE'`;
- adds constraints which reject `ARCHIVE` rows without an object key and reject `PACKAGES_ONLY` rows with an object key, without blocking the separate `WORKSPACE_REVISION` design;
- can run safely on an existing PostgreSQL deployment.

Update Pydantic/type annotations and test factories that assume `object_key` is always a string.

### 3.3 Submission semantics

Change `POST /jobs/submit_job` so `zip_file` is optional.

- A present, named ZIP follows the existing validation and Object Store upload path and creates `source_kind='ARCHIVE'`.
- An omitted file part creates `source_kind='PACKAGES_ONLY'`, stores `object_key=None`, and does not call `save_to_object_store`.
- A supplied but empty or invalid file is still an invalid upload; do not silently reinterpret an upload error as a package-only request.
- Preserve name, selected base image, packages, priority, and command compatibility fields.
- Treat packages as optional: no ZIP plus no packages is a valid base-image-only interactive environment.
- Return the provenance/capability fields in the create response and all job detail/list responses.

The image-builder claim payload must include `source_kind`, nullable `object_key`, packages, base image, job ID, builder ID, and attempt ID.

## 4. Scheduler implementation

### 4.1 Persistence and serialization

Update:

- `Scheduler/app/models/job_model.py`
- `Scheduler/app/schemas/job_schema.py`
- `Scheduler/app/services/job_service.py`
- `Scheduler/app/api/jobs_route.py`
- a new migration after `004_decouple_image_and_training.sql`

Map `source_kind` in the ORM and allow `object_key=None`. Set both fields in `create_job`. Include `source_kind` and `training_eligible` in `_format_job_response`, `get_not_runnable_jobs`, `get_user_jobs`, `get_user_job_by_id`, and any create/detail response relied on by the User UI.

Keep package-only jobs in the normal image states:

```text
NOT_RUNNABLE -> IMAGE_BUILDING -> IMAGE_READY
```

Do not introduce a second status for this feature. `IMAGE_READY` describes build state; `training_eligible` describes permitted use.

### 4.2 Server-side training guard

In `job_service.submit_training`, after ownership lookup and before any mutation, reject `source_kind == 'PACKAGES_ONLY'` with HTTP 409 and a stable, user-facing message such as:

> This image has no workspace files and is interactive-only. Open it as an interactive workspace, add and save files, then submit the saved workspace for training.

The rejected request must not set commands, priority, VRAM fields, or status. Keep the existing status/image checks for trainable jobs.

If the older submission route accepts a `command` at image-creation time, reject a command on a package-only submission as well so it cannot jump directly to `VRAM_ESTIMATION_PENDING` in `set_job_image_ready`.

### 4.3 Preserve interactive eligibility

`GET /interactive/workspaces/source-jobs` already selects owned jobs with a published image across `IMAGE_JOB_STATES`. Keep `PACKAGES_ONLY` images in this result. Optionally return `source_kind` so the UI can label them “Interactive-only”; do not filter them out because this is their intended use.

The existing `from-job` interactive path should remain the handoff:

```text
PACKAGES_ONLY batch image
  -> /interactive/workspaces/from-job
  -> interactive revision derived from the published image
  -> runtime/editor/terminal
```

Do not send the package-only batch job to a training Worker, and do not duplicate it into the separate interactive upload/build queue during initial submission.

### 4.4 Adjacent nullable-object behavior

Audit all `job.object_key` consumers. The output bundle path already accepts `submitted_object_key: str | None`; retain that behavior so package-only jobs can still download later output/log artifacts without an original submitted ZIP. Ensure logging and JSON serialization do not turn `None` into the string `"None"`.

## 5. Docker image builder implementation

### 5.1 Branch by `source_kind`, not truthiness alone

Update `Docker_Image_Builder/builder.py` claim validation and processing:

- Always require job ID, base image, and build attempt ID.
- For `ARCHIVE`, require `object_key`, download it, safely extract it, locate the project, and run the existing build.
- For `PACKAGES_ONLY`, require no archive download/extraction. Create a temporary empty project/build source and call the shared build logic with `include_project=False` (or an equivalently explicit argument).
- Reject inconsistent/unknown payloads as malformed and release them through the existing fenced retry path. Do not guess a source type from a transient missing field.
- Clean up every temporary directory in `finally`, and preserve lease cancellation checks and callback fencing.

### 5.2 Ensure `/workspace` really has no uploaded files

Update `Docker_Image_Builder/docker_ops.py` so `generate_dockerfile` and `build_push_and_clean` take an explicit `include_project`/`has_archive` option.

For archive builds, preserve the existing copy behavior. For package-only builds, generate the equivalent of:

```dockerfile
FROM <allowlisted/validated selected base>
WORKDIR /workspace
RUN pip install --no-cache-dir '<package-1>' '<package-2>'
CMD ["python"]
```

Omit the `COPY` instruction entirely when there is no ZIP. This is required because a blanket `COPY . /workspace/` would copy the generated Dockerfile itself and violate the “no workspace files” promise. If there are no packages, omit the `RUN pip install` instruction and build the base-derived image.

Preserve current package parsing, error classification, per-base build locking, cancellable Docker build, push behavior, attempt-specific tags, and local cleanup. Add a build-log line that clearly states `Workspace files: none (package-only image)` so operators and users can distinguish this intentional branch from a missing-object failure.

Do not change `Docker_Image_Builder/interactive_build.py` merely to implement initial package installation. The package-only image enters interactive access through the existing `EXISTING_JOB` source path, which resolves and wraps the published image by digest.

## 6. User UI implementation

### 6.1 Optional ZIP and confirmation pop-up

Update `UI/User/src/pages/SubmitJob.tsx`:

- Mark the archive as optional and change empty-state copy from “No file yet” to “No archive — interactive-only image”.
- Remove the current missing-ZIP validation error.
- On form submit with no ZIP, pause submission and open a real modal/alert dialog while retaining all entered form values.
- The dialog must state:
  - no files will be placed in `/workspace`;
  - listed packages will be installed (or the base image will be used as-is if none are listed);
  - this image cannot be sent directly to training;
  - it can be opened interactively so files can be created in the container/editor.
- Provide **Go back** and **Build interactive-only image** actions. Confirmation must invoke the same guarded submit exactly once. Disable actions while the request is in flight.
- Support keyboard/focus behavior expected of an accessible modal: `role="alertdialog"`, `aria-modal`, labelled title, Escape/backdrop close when not submitting, initial focus, and focus return.
- Update the hero summary and “Next step” card dynamically: ZIP-backed submissions lead to training; no-ZIP submissions lead to interactive access.

Reuse or generalize the existing dialog styling instead of rendering a replacement page. The current `InteractiveCreate.tsx` confirmation proves the wording/flow but is not currently a modal overlay and incorrectly says there are no extra packages; keep the two flows consistent after adding package-aware copy.

### 6.2 Multipart client

Update `UI/User/src/services/jobs.ts`:

- change `submitJob(..., zipFile)` to accept `File | null`;
- append `zip_file` only when a file exists;
- extend `BackendJob` and `Job` with `sourceKind` and `trainingEligible` (use safe defaults for legacy/mock responses);
- retain browser-generated multipart boundaries and current authentication/error handling.

Do not send a client-controlled `training_eligible` value. It is server-owned.

### 6.3 Remove misleading training actions everywhere

Capability-gate all training entry points, not just the submission form:

- `UI/User/src/pages/Builds.tsx`
- `UI/User/src/pages/BuildDetails.tsx`
- `UI/User/src/pages/JobDetails.tsx`
- `UI/User/src/pages/Training.tsx`
- any dashboard card found during implementation that links to training

For a ready package-only image:

- never show an enabled “Start training” action;
- show an “Interactive only / no workspace files” label or explanatory callout;
- offer “Use interactively” as the primary next action;
- if a user pastes/navigates directly to its ID on the Training page, disable submission and show the server-aligned reason.

The UI guard is explanatory only; the Scheduler guard remains authoritative.

### 6.4 Make the interactive handoff direct

Allow a ready build to link to a preselected existing-job flow, for example:

```text
/submit?mode=interactive&source=job&job=<job-id>
```

Update `InteractiveCreate.tsx` to read those query parameters after loading owned source jobs, select the requested job only if it is present, and otherwise show the normal selector. Do not trust a query-string ID that is absent from the owned choices. The user still supplies/edits an interactive workspace name and explicitly creates it through the existing idempotent API.

## 7. Tests

### 7.1 Scheduler tests

Extend `Scheduler/test/unit/test_api_routes.py`, `test_job_service.py`, `test_interactive_workspaces.py`, and helpers as appropriate:

- no-file multipart submission succeeds, skips Object Store upload, stores `object_key=None`, `source_kind='PACKAGES_ONLY'`, packages, and `NOT_RUNNABLE`;
- present valid ZIP remains `ARCHIVE` and unchanged;
- present invalid/empty ZIP fails rather than becoming package-only;
- package-only claim serialization includes nullable object key and provenance;
- successful package-only build callback stops at `IMAGE_READY`;
- direct training submission for package-only returns 409 and leaves the row unchanged;
- archive-backed `IMAGE_READY` training still advances to `VRAM_ESTIMATION_PENDING`;
- package-only image remains listed as an interactive source for its owner and not for another user;
- output download tolerates a missing submitted object;
- migration/model defaults preserve existing rows as archive-backed.

### 7.2 Builder unit tests

Extend `Docker_Image_Builder/test/unit/test_builder.py` and `test_docker_ops.py`:

- a `PACKAGES_ONLY` claim does not call archive download, ZIP extraction, or project discovery;
- it calls the shared build path with project copying disabled and passes packages unchanged;
- its generated Dockerfile has `WORKDIR` and package installation but no `COPY`;
- no packages yields a valid base-derived Dockerfile with no pip step and no `COPY`;
- archive claims retain `COPY` and current requirements/package precedence;
- `ARCHIVE` without an object key and `PACKAGES_ONLY` with a conflicting object key are rejected/released;
- temporary directories are removed on success, cancellation, user failure, and system failure;
- build logs identify package-only builds;
- lease cancellation and ready/failure callbacks remain fenced.

Add a Docker-backed integration test if the existing CI profile can run it: build a package-only image, assert `/workspace` has no copied files, assert a requested package imports, and clean up the test tag/image.

### 7.3 User UI tests

Add focused Vitest/Testing Library coverage (and add a script if needed) for:

- ZIP present submits immediately and includes `zip_file`;
- ZIP absent opens the warning and sends no request before confirmation;
- cancel returns to the intact form;
- confirm sends exactly one request without `zip_file`, including packages;
- modal copy distinguishes packages-selected and base-only cases;
- package-only job mapping exposes `trainingEligible=false`;
- build/list/detail pages show interactive CTA and no training CTA;
- Training direct navigation is disabled with the correct explanation;
- interactive CTA safely preselects the owned source job.

## 8. Verification sequence

Run the narrow suites first, then the component suites:

```bash
cd Docker_Image_Builder && pytest -q test/unit/test_builder.py test/unit/test_docker_ops.py
cd Scheduler && pytest -q test/unit/test_api_routes.py test/unit/test_job_service.py test/unit/test_interactive_workspaces.py test/unit/test_output_download.py
cd UI/User && npm run lint && npm run test:interactive && npm run build
```

Then run the full Scheduler and Builder unit suites. If Docker/registry integration credentials are available, run the relevant opt-in builder integration test as well.

Perform a manual end-to-end check in a disposable environment:

1. Create a workspace with a ZIP and verify its old build/training flow still works.
2. Create one without a ZIP but with a small package such as a pinned pure-Python dependency.
3. Verify the warning appears and no request occurs before confirmation.
4. Verify no upload object is created for the package-only job.
5. Verify build logs show package-only mode and the image reaches `IMAGE_READY`.
6. Verify direct `POST /jobs/{id}/training` returns 409 and every User UI training entry point is unavailable.
7. Choose **Use interactively**, create an interactive workspace from that image, start it, and confirm the requested package imports.
8. Confirm `/workspace` begins without uploaded project files, then create a file through the editor/terminal.
9. Verify another user cannot select or open the source image.
10. Verify cancellation, retry, and cleanup leave no local temporary directory or stale local image.

## 9. Rollout order and compatibility

Deploy in this order:

1. Scheduler migration/model/API/service support.
2. Docker Image Builder support for `PACKAGES_ONLY` claims.
3. User UI optional-upload workflow and capability-aware actions.

During a rolling deploy, do not let the Scheduler issue `PACKAGES_ONLY` work to old builders that require `object_key`. Either deploy/drain builders before exposing the UI or add a builder capability/version to claims. Merely releasing malformed claims would create an endless retry loop.

Observe counts and failures by `source_kind`: submissions, build duration, pip/user failures, system retries, training-guard 409s, and interactive-workspace creation from package-only images. Rollback the UI first to stop new submissions; already-created package-only rows remain safe because the Scheduler training guard is deployed first.

## 10. Definition of done

- A user can intentionally confirm a no-ZIP workspace build.
- The built image contains the base environment and requested packages, with no copied/generated file placed in `/workspace` by this flow.
- The job reaches `IMAGE_READY`, appears in build history, and is selectable as an owned interactive source.
- Starting interactive access from it works and permits the normal editor/terminal file workflow.
- Direct training is blocked both in every User UI surface and by the authenticated Scheduler endpoint.
- ZIP-backed jobs and direct interactive uploads have no regressions.
- Unit, UI, migration, and available end-to-end tests pass, with results recorded in the implementation handoff.
