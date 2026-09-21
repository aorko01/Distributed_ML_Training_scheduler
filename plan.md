# Plan — Option B: Opt-in internet in interactive workload + portable persisted image

Status: plan only, no code changed. Implements the user's chosen Option B and the
portability requirement: any `pip install` done in the web editor/terminal
(whether listed in `requirements.txt` or not) must persist into a portable
image runnable elsewhere with only a start command.

## 1. Goal and non-goals

Goal:

1. Operator opt-in egress for the interactive workload so `pip install matplotlib`,
   `load_dataset("imdb")`, `apt`, `curl`, `git` work from the web editor terminal.
2. Durable `Save for Later` / `Submit for Training` that captures the exact
   workload filesystem (code edits + installed packages + terminal-created files)
   into an immutable portable image digest.
3. That digest runs on another machine with only `docker run --gpus ... <digest>
   python train.py ...` (or Start-from-revision / batch job from same digest).

Non-goals:

- No public inbound ports to workload, no SSH daemon, no Docker socket in
  workload/Access/browser, no registry push creds on Worker, no browser-chosen
  container/image/registry/path.
- Do not silently relax isolation. Default stays `network_mode=none`. Internet is
  an explicit operator flag, surfaced in UI as capability.
- No language server, debugger, multi-terminal, port preview. One PTY + bounded
  file ops as today.

Current truth (verify before coding):

- `Worker/interactive/docker_ops.py:303` `workload()` hardcodes
  `network_mode="none", cap_drop=["ALL"], no-new-privileges:true`. This is why
  `pip` logs `Temporary failure in name resolution` and `load_dataset` logs
  `Couldn't reach 'imdb' on the Hub`.
- `Docker_Image_Builder/interactive_build.py:85-104` bakes `requirements.txt`
  at build time only. Terminal installs go to the container writable layer and
  are discarded on Stop (`docs/interactive-runtime-operations.md:4`).
- `UI/.../IDETitleBar.tsx:16-17` disables Save/Submit; `Scheduler/.../scheduling/config.py:28-29`
  `WORKSPACE_SAVE_ENABLED / WORKSPACE_TRAINING_SUBMISSION_ENABLED` must stay off
  until this pipeline is done. Models/migration exist
  (`interactive_workspace_model.py:76-127`, `migrations/003_workspace_editor_snapshots.sql`)
  but Worker capture + Builder SNAPSHOT import + private artifact store are missing.

## 2. Decisions (do not deviate without updating this plan)

1. `INTERACTIVE_ALLOW_INTERNET=1` on Worker enables egress for new workloads only.
   Default `0`/unset = current `none` behavior. Scheduler advertises capability
   via `INTERACTIVE_INTERNET_ENABLED=1` + per-runtime `launch_spec.allow_internet`
   so UI can explain why pip fails when off.
2. Workload still: `cap_drop ALL`, `no-new-privileges`, `init`, `pids/cpu/ram/swap/
   storage quota`, single `gpu_uuid`, no volumes, no env secrets, no published
   ports, `USER 10001:10001`, `WORKDIR /workspace`. Only `network_mode` changes
   (`none` -> `bridge`/default + default DNS). No `--privileged`, no host net.
3. Persistence = `docker commit --pause` of the exact labelled workload, then
   `docker save` -> private snapshot artifact -> Builder `docker load` + validate
   + push digest-pinned tag. Never commit sidecar/access. Never `docker system
   prune`.
4. Saved image is command-independent and immutable. Training executability is a
   separate derived image (Builder sets exec-form `CMD ["python",...]` from the
   validated training command) or `docker run <saved-digest> <command>`. Do not
   bake keepalive (`sleep` loop) into saved/training images.
5. `pip` target must live in image filesystem: system site (`/usr/local`) or
   user site (`~/.local`) inside writable layer. Forbid installs to mounted
   volumes (there are none) and forbid `PIP_TARGET=/tmp` style escapes. Clean
   `~/.cache/pip` optionally, keep `site-packages`.

## 3. End-to-end flow after this plan

```text
Upload/ExistingJob -> Builder builds base IMAGE_READY (unchanged)
  -> Start runtime (launch_spec {allow_internet}) -> Worker workload
     [bridge iff allow_internet else none] + broker + sidecar/access + tailnet 9000
  -> Browser workspace-stream-v1: files + PTY (pip install works iff internet on)
  -> Save for Later: flush buffers, gate PTY/mutations, docker commit --pause,
     docker save, upload artifact (scoped cap), Builder load/validate/push,
     new SNAPSHOT revision IMAGE_READY (saved_revision_id advances)
  -> Submit for Training: same capture, then Stop/release runtime, create batch
     Job with provenance {workspace, revision, digest, command}, Builder derives
     executable training image CMD, normal VRAM-estimate -> training
  -> Portability: docker pull <saved-digest> && docker run --gpus all <digest>
     python train.py ... works on any host (deps + code baked)
```

## 4. Implementation phases

### Phase 0 — Recon (read-only, 0.5 day)

Read and cite actual code, do not assume:

- `Worker/interactive/docker_ops.py`, `manager.py`, `broker.py`,
  `workspace_broker.py`, `file_service.py`, `endpoint.py`, `cleanup.py`
- `Scheduler/app/services/scheduling/config.py`, `interactive_runtime_service.py`,
  `interactive_workspace_service.py`, `workspace_editor_service.py`,
  `app/api/worker_execution_route.py`, `app/schemas/worker_execution_schema.py`
- `Docker_Image_Builder/interactive_build.py`, `interactive_api.py`, `docker_ops.py`,
  `config.py`, `database.py`
- `Access_Container/interactive_access/{session,workspace_protocol,protocol}.py`
- `Gateway/interactive_gateway/relay.py`
- `UI/User/src/features/workspace/*`, `pages/InteractiveEditor.tsx`,
  `services/{interactive,workspaceProtocol}.ts`
- `docs/workspace-stream-v1.md`, `docs/interactive-*.md`,
  `Worker/.env.example`, `Scheduler/.env.runtime.example`,
  `deploy/interactive/*`, `test/interactive_e2e/*`

Output: confirm save/submission flags off, confirm no SNAPSHOT builder branch,
confirm no snapshot artifact routes.

### Phase 1 — Opt-in workload egress (Worker + Scheduler contract)

Files:

- `Worker/interactive/docker_ops.py:272-314` — add:
  ```python
  def workload_internet_enabled(record=None) -> bool:
      # Worker hard gate; Scheduler hint is advisory only.
      return os.getenv("INTERACTIVE_ALLOW_INTERNET","0").strip().lower() in ("1","true","yes")
  ```
  In `workload()`: `network = None(default bridge) if enabled else "none"`.
  Pass `network_mode` conditionally (`docker-py`: omit key or `None` for bridge;
  do not pass `"bridge"` string unless daemon requires it — test both).
  Keep `cap_drop`, `security_opt`, `init`, quotas, `device_requests` unchanged.
  Log `network=none|bridge` with assignment id (no secrets).
  Keep `preflight()` at `none`.
- `Scheduler/app/services/scheduling/config.py:41-56` — add to profile:
  `allow_internet: os.getenv("INTERACTIVE_INTERNET_ENABLED","0")=="1"`.
  Validate bool. Include in `launch_spec` persisted on runtime/assignment.
  Worker MUST re-check its own `INTERACTIVE_ALLOW_INTERNET`; if Scheduler says
  allow but Worker is `0`, launch with `none` and report `INTERNET_DISABLED`
  health/failure detail (fail closed, do not auto-enable).
- `Scheduler/app/schemas/worker_execution_schema.py`,
  `app/api/worker_execution_route.py`, `Worker/managed_worker.py`,
  `Worker/execution_state.py` — plumb `launch_spec.allow_internet` through claim
  payload; no browser input ever sets it.
- `Worker/.env.example`, `Scheduler/.env.runtime.example`,
  `docs/interactive-runtime-operations.md` — document both flags, default off,
  restart required, how to verify (`docker inspect --format '{{.HostConfig.NetworkMode}}'`).

Tests:

- Update `Worker/test/unit/test_runtime_docker.py:117-139`: parametrize
  `INTERACTIVE_ALLOW_INTERNET=0 -> none`, `=1 -> bridge/default (assert key
  absent or None, plus no volumes/env/ports, single GPU)`.
- Add Scheduler config test: default false, `1` true, invalid does not crash.
- Manual: `INTERACTIVE_ALLOW_INTERNET=1` workload `pip install matplotlib`,
  `python -c "import matplotlib"`, `nslookup pypi.org`, `curl -I https://huggingface.co`
  succeed; with `0` they fail with DNS error and UI shows capability off.

Security notes: no `--dns` override needed (use host default); no exit-node;
no `SOCKS` from endpoint (`endpoint.py` unchanged); Gateway stays byte relay.
Record egress enablement in assignment journal for audit.

### Phase 2 — Durable capture: commit + artifact upload (Worker)

This is the portability core. Reuse existing `WorkspaceSaveOperation` states:
`REQUESTED->CAPTURING->UPLOADING->PUBLISH_QUEUED->PUBLISHING->SUCCEEDED`
(`FAILED`/`CANCELLED` on controlled failure).

Worker tasks (new module `Worker/interactive/snapshot.py` + hooks in
`manager.py`, `broker.py`, `workspace_broker.py`):

1. Discovery: poll authenticated execution API for pending save ops for held
   assignment (or heartbeat instruction). Persist `operation_id/capture_attempt_id`
   in journal before any Docker side effect. One active save per runtime/generation;
   second request gets `409 BUSY`.
2. Gate: set read-only/capture phase, drain in-flight file mutations (FileService
   queue), reject new `write/create/mkdir/rename/delete` with `READ_ONLY_CAPTURE`
   and new `PTY_OPEN` with `CAPTURE_IN_PROGRESS`, notify client via
   `WORKSPACE_STATE`. Close/reap current PTY using existing pidfd/exact-container
   guarantees (`broker.py:DockerSession.close`). Pause background processes is
   best-effort; document no DB-transaction guarantee.
3. Commit: revalidate lease/authority, then exact-label check, then:
   ```sh
   docker commit --pause <workload-id> <staging-tag:operation-id>
   docker inspect <staging-tag>  # capture Image ID, User, WorkingDir, Env
   ```
   Preserve source `User (10001:10001)` and `WorkingDir (/workspace)`; strip
   runtime keepalive `Entrypoint/CMD` (set to `["/bin/sh"]` placeholder, real CMD
   set at training-derivation time); strip runtime labels; reject if image has
   `Volumes`. Persist `image_id` in journal immediately.
4. Export: `docker save <image-id> | gzip` streamed (never whole image in RAM)
   to private snapshot artifact storage using short-lived op-scoped capability
   (see Phase 2b). Record `sha256/size`. Enforce size/disk headroom
   (`spec.disk_gb + headroom`), deadlines (capture 5 min, upload 20 min), cancel
   on lease loss. On success reopen editor/terminal (Save for Later) or proceed
   to Stop (Submit).
5. Cleanup: `docker rmi <staging-tag>` after Builder accepts; reconcile late
   commit after Stop/lease-loss by exact `operation_id` tag — never publish into
   new generation. No global prune.

Failure handling: hung commit past pause-deadline -> bounded `docker unpause`,
deny further captures, quarantine, report `CAPTURE_TIMEOUT`. All exit paths
restore gate or go to exact Stop cleanup.

### Phase 2b — Private snapshot artifact API (Scheduler + Object_store)

Do NOT reuse generic object upload/presign routes.

- Add `Scheduler/migrations/004_snapshot_artifacts.sql` if needed (check `003`):
  `workspace_snapshot_artifacts(id, operation_id UNIQUE, sha256, size, receipt,
  created_at)` + capability table with expiry. Secrets never in DB.
- Add Scheduler routes: `POST /internal/saves/{op}/upload-capability`
  (Worker auth, returns single-use URL + headers), `POST /internal/saves/{op}/complete`
  (Worker sends sha/size/receipt), `GET /internal/builder/snapshot/{artifact}` (Builder
  auth, streamed read). Validate Worker id + assignment + attempt_token +
  generation on every call. Stale Worker receipt rejected.
- `Object_store`: new private bucket/prefix `snapshots/` with distinct service
  creds, multipart/streamed upload, immutable keys
  `snapshots/<workspace>/<revision>/<attempt>/<sha>.tar.gz`, no overwrite,
  abort only exact abandoned upload.
- Scheduler verifies receipt/size/hash before inserting `PUBLISH_QUEUED` and
  enqueuing Builder SNAPSHOT claim. Browser never sees capabilities.

### Phase 3 — Builder SNAPSHOT import + portable + training-derivable image

Extend `Docker_Image_Builder/interactive_build.py`:

- New `origin=="SNAPSHOT"` branch (do not feed to ZIP extractor):
  1. Download artifact via authorized Builder read, verify sha/size.
  2. `docker load -i artifact.tar.gz`, resolve loaded Image ID, verify
     `Os/Arch == launch_spec.platform`, `User == 10001:10001` (or allow_root
     policy), `WorkingDir == /workspace`, no `Volumes`, size bounds.
  3. Tag `interactive-{workspace}:revision-{rev}-attempt-{attempt}` (reuse
     `tag_for`), push with Builder-only creds, resolve digest, callback `ready`
     with `image_digest_ref`. Mark revision `IMAGE_READY`, advance
     `saved_revision_id` with CAS (do not clobber newer head on late callback).
  4. Training derivation (for Submit): `FROM <saved-digest>`, set exec-form
     `CMD` from validated training settings (e.g. `["python","train.py","--epochs","10"]`),
     clear `ENTRYPOINT`, push `training-{job}:...`, record digest on Job.
     Never rerun `pip install`, never replace `/workspace` with original upload.
- Keep `UPLOAD/EXISTING_JOB` paths unchanged. Add `test/unit/test_snapshot_import.py`
  + e2e with tiny fixture proving: terminal `pip install` + file edit survive
  `docker pull <new-digest>` on another host and `docker run <digest> python train.py`
  works.

Portability contract to document: saved image contains `/workspace` code +
`site-packages` (`/usr/local/lib/python*/site-packages` + `~/.local`) + HF cache
if user left it. Processes/env exports/GPU state not restored. `requirements.txt`
edit alone does not install — user must `pip install` (now possible) before Save.

### Phase 4 — Scheduler submission -> batch job (provenance intact)

- `workspace_editor_service.py:create_save/create_submission` already gates flags;
  wire `WORKSPACE_SAVE_ENABLED=1` only after Phases 2-3 pass. Keep idempotency-key
  semantics, `409` on reuse with different body, one active save per runtime.
- Submission sequence: `SAVING->WAITING_FOR_REVISION->STOPPING_RUNTIME->
  WAITING_FOR_RELEASE->PREPARING_JOB->JOB_CREATED`. After `SUCCEEDED` publication,
  request runtime Stop, revoke grants/enrollment, await exact cleanup + release
  ack (never force-clear hold on timeout), then create ONE batch Job with
  `source_kind=SNAPSHOT`, `source_workspace/revision/digest`, validated
  `command/resume_command/priority`. Normal `IMAGE_BUILDING->VRAM_ESTIMATION->
  RUNNABLE->IN_PROGRESS` pipeline runs it.
- UI: enable `Save for Later` / `Submit for Training` buttons only when
  `editor_capable && IMAGE_READY && save_enabled`, show `Written to workspace ·
  save a revision to keep it after Stop`, progress `Saving->Publishing->
  Stopping->Queued`, `View Training Job` link, reload-safe status via
  `GET /interactive/saves/{id}`.

### Phase 5 — Tests and gates (must all pass before flags on)

- Unit: Worker network gate, Builder SNAPSHOT validation (volumes/user/arch/size
  reject), Scheduler save/submission idempotency + CAS head advance, FileService
  traversal/symlink/hardlink/FIFO/binary/2MiB/CONFLICT still pass.
- Protocol: workspace-stream-v1 HELLO/READY, file chunks, PTY_OPENED/STDOUT/EXIT,
  BUSY, READ_ONLY_CAPTURE, frame split/coalesce, unknown record fail-closed.
- E2E (real Docker, disposable registry, separate hosts):
  1. Start -> Connect -> Open Editor -> create/edit file -> Save All verified
     inside workload (`docker exec` cat).
  2. `pip install <tiny-pinned-fixture>` in web terminal, `import` succeeds.
  3. Terminal-created file + pip package + editor edit all present after
     `Save for Later` + `docker pull <new-digest>` on second host.
  4. `Submit for Training` with `python train.py ...` yields batch job that
     estimates + trains + uploads outputs; original revision immutable.
  5. Double-click/reload creates no duplicate revision/job; Stop during capture
     reconciles without leaking images; secret scan clean.
- GPU acceptance: assigned UUID only, quotas enforced, batch priority preserved.

### Phase 6 — Rollout and operator runbook

Worker `/etc/dml/worker.env` (add):

```ini
INTERACTIVE_ALLOW_INTERNET=1   # 0 = offline (default). Requires restart.
```

Scheduler env (add):

```ini
INTERACTIVE_INTERNET_ENABLED=1
WORKSPACE_EDITOR_ENABLED=1
WORKSPACE_SAVE_ENABLED=1            # only after Phases 2-3 green
WORKSPACE_TRAINING_SUBMISSION_ENABLED=1  # only after Phase 4 green
```

Gateway/Headscale/Object_store: no new public ports. Ensure Worker has outbound
443 to PyPI (`pypi.org/files.pythonhosted.org`), HF (`huggingface.co/cdn-lfs`),
plus registry + Scheduler + Headscale. If corporate proxy needed, set
`HTTP(S)_PROXY` on daemon (not in workload env) and document.

Verify:

```sh
docker inspect dml-<assignment>-workload --format '{{.HostConfig.NetworkMode}} {{.Config.User}} {{.Config.WorkingDir}}'
docker exec dml-<assignment>-workload pip install matplotlib
docker exec dml-<assignment>-workload python -c "import matplotlib; print('ok')"
# after Save:
docker pull <saved-digest> && docker run --rm --gpus all <saved-digest> python train.py --epochs 1
```

Rollback: set both internet flags `0`, new runtimes go `none`; existing bridge
runtimes keep running until Stop (document). Disable save/submission flags to
hide buttons without breaking live editor. No migration downgrade; additive only.

## 5. Acceptance criteria

- `pip install matplotlib` + `load_dataset("imdb")` succeed in editor terminal
  when flags `1`, fail with clear `Internet disabled by operator` message when `0`.
- Packages installed via terminal (in or out of `requirements.txt`) + code edits
  survive `Save for Later` and run on another host via only the start/run command.
- No creds/tickets/keys in image layers, logs, or browser storage (secret scan).
- All existing terminal-only runtimes still work; `none` remains default.
