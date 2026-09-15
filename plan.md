# Plan: Concurrent job execution on the Worker — post-implementation review

## Verdict

The uncommitted changes are a **substantial, mostly correct** implementation of the
concurrency work, and the unit suite is green. Every issue from the earlier audit was
addressed except the two that matter most for correctness under concurrency:

- **capacity is still not reserved synchronously before a worker thread starts** (the P2
  race window is open), and
- **resume is spawned on every poll without any in-flight guard**, so the same persisted
  job can be resumed several times concurrently (the P4 race window is open).

Both are timing windows that only appear when the background thread has not yet run its
first statement. Existing tests cannot see them because they freeze
`active_jobs_count` / mock `threading.Thread`.

There is also one open P1-family bug: a single malformed persisted entry (`{}` with no
`job_id`) calls `clear_running_job(None)` and **deletes the whole state file**, wiping the
resume markers of all other concurrent jobs.

This plan records those remaining source issues (do **not** fix source here), and the
tests added to pin them down.

---

## Part 1 — Status of the original findings

| ID | Finding | Status | Notes |
|---|---|---|---|
| P1 | `clear_running_job()` (no arg) wipes all persisted jobs on finalize | **Fixed** | `executor.py:610` now `clear_running_job(job_id)`; call sites 387/405/610/775 pass an id. |
| P2 | Capacity double-count `active_jobs_count + len(active_threads)` | **Partially fixed** | The sum is gone (`main.py:48`), but there is no synchronous reservation → **R1** below. |
| P3 | Successful resume leaks a capacity slot | **Fixed** | `try/finally: self._unregister_job(job_id)` at `executor.py:772-784`; covered by tests. |
| P4 | Resume moved out of the polling loop / blocks startup | **Partially fixed** | Resume now runs on a daemon thread from `job_loop` and `main()` starts the server promptly, but it is not deduplicated → **R2** below. |
| P5 | `pull_job` used raw free VRAM | **Fixed** | `main.py:55-56` passes `get_effective_free_vram(...)`; test `test_effective_vram_passed_to_pull` asserts it. |
| P6 | Shared legacy log buffers collected every job's output | **Fixed** | Shared buffers removed; per-job `_JobLogState` used (`executor.py:30-36`, `514`). Minor leftover None branches (R4). |
| P7 | Duplicated dead block in `job_state.py` | **Fixed** | Single clean module now. |
| P8 | `running_job.json` unignored / written by a test | **Fixed** | `Worker/.gitignore` added; `save_running_job` patched in `test_process_job_tracks_active`; stray file gone. |
| P9 | Thread naming / minor issues | **Fixed** | Thread name resolves `job_id or id` (`main.py:60`). |

Suite: `257 passed, 3 xfailed` (the 3 xfails are the new regression tests for R1–R3).

---

## Part 2 — Remaining source issues

### R1 (Critical) — Capacity is not reserved before the worker thread starts
**File:** `Worker/main.py:48-63`

```python
if executor.active_jobs_count < max_jobs:
    ...
    job = api.pull_job(gpu_type, effective_free)
    if job:
        t = threading.Thread(target=executor.process_job, args=(job,), ...)
        t.start()                       # registration happens *inside* this thread
        active_threads.append(t)
```

`process_job` only calls `_register_job` after the new thread is scheduled
(`executor.py:687`). Between `t.start()` and that call, `active_jobs_count` is still
unchanged, so the loop is free to pull again — it can start far more than
`max_concurrent_jobs` jobs. It is worst for `max_concurrent_jobs=1`, where the loop only
stops pulling if the thread happens to register before the next `pull_job` returns.

Reproduced deterministically (slow-registering `process_job`, bounded loop,
`max_jobs=2`): **8 jobs pulled instead of ≤2**.

**Fix:** add a synchronous reservation on the executor (e.g.
`reserve_job_slot(job) -> str | None` / `try_begin_job(job_id, vram) -> bool`) that
registers under `_active_jobs_lock` and returns `None`/`False` for a duplicate or when
the slot is already taken. Call it in `job_loop` **before** `t.start()` and only start
the thread on success. Keep `active_jobs_count` as the single capacity source and keep
`process_job`'s `finally: _unregister_job` so the slot is released exactly once when the
job truly ends. Make `process_job`'s registration idempotent (no-op / skip if already
registered) so reserving in the loop and registering again in the thread is harmless.

### R2 (Critical) — Resume can be spawned repeatedly for the same job
**File:** `Worker/main.py:49-53`, `Worker/executor.py:716-785`

```python
if executor.has_unresumed_job():
    threading.Thread(
        target=executor.resume_persisted_job_if_any,
        daemon=True, name="resume",
    ).start()
```

`has_unresumed_job()` returns `True` until the job is registered in `_active_jobs`, and
`resume_persisted_job_if_any` registers only after the (network) `api.resume_job` call
returns (`executor.py:754-766`). During that window every poll iteration spawns another
resume thread; all of them can call `api.resume_job` and then `handle_retry` for the same
job, i.e. duplicate container runs and racing writes to the same output dir.

Reproduced deterministically (slow resume, bounded loop, one persisted job):
**20 resume attempts instead of 1**.

**Fix:** make resume obey the same single source of truth. Options:
1. Have `job_loop` synchronously reserve the persisted job (R1's `reserve_job_slot`)
   before spawning the resume thread, or
2. track in-flight resumes in the executor (a `_resuming: set[str]` guarded by a lock,
   or reuse `_active_jobs`) so `has_unresumed_job` is `False` from the moment a resume
   starts, not when it finishes registering.

Either way `is_job_active`/the reservation must be set before the thread is spawned.

### R3 (High) — A malformed persisted entry clears the state of every job
**File:** `Worker/executor.py:746-749`

```python
job_id = item.get("job_id")
if not job_id:
    clear_running_job(job_id)      # job_id is None -> deletes the whole file
    continue
```

`clear_running_job(None)` removes `running_job.json` entirely (`job_state.py:104-106`).
So one corrupt/partial entry (e.g. a bad write, or a pre-migration artifact) discards the
resume markers of **all** other in-flight jobs — the same blast radius as the original P1.

Reproduced: `load_running_jobs() == [{...no job_id...}, {"job_id": "j2"}]` produces
`clear_running_job(None)` followed by `clear_running_job("j2")`, i.e. j2's marker is
destroyed via the clear-all before it is even evaluated.

**Fix:** for an entry without an id, do not clear anything (or add a
`clear_running_job_entry(item)`/index-based removal that only drops that entry). Never
call the no-arg clear from the resume loop. `clear_running_job()` (clear-all) should only
be reachable from an explicit "reset" tool/route.

### R4 (Low) — Leftovers from the legacy single-job log path
**File:** `Worker/executor.py:309-329`, `621-643`

`_get_job_log_state(None)` and `_reset_log_state(None)` still exist and silently no-op to
support old tests, and `_flush_log_push`/`_append_build_log` still branch on `state is
None`. The plan wanted per-job state to be mandatory (always pass a `job_id`). This is
not a functional bug (all real call sites pass an id) but the dead branches are exactly
what let bugs hide behind "legacy" paths before; removing them reduces bug surface.

### R5 (Low) — Resume path skips the job_id traversal check
**File:** `Worker/executor.py:746-768`

`process_job` rejects ids containing `/`, `\`, or `..` (`executor.py:677-683`), but
`resume_persisted_job_if_any` builds `image_name` and the output dir from a persisted
`job_id` without re-validating. The value normally came from a job that passed the check
originally, so impact is low, but it should be validated when read back from disk.

### R6 (Low) — Redundant shutdown join
**File:** `Worker/main.py:70-75`

The loop body joins `active_threads` after the `while`, and the `finally` joins them
again. Harmless; keep the `finally` only, or make the intent explicit (daemon training
threads are abandoned after the 1s timeout — worth a comment).

---

## Part 3 — Test changes made (this pass)

The suite already gained good coverage of P1/P3/P5/P7/P8 and the persistence helpers. The
remaining gaps were exactly the timing windows above, so three focused regression tests
were added. They are marked `@pytest.mark.xfail(strict=True)` so CI stays green while the
source bugs are open, and they will turn into hard failures (XPASS → fail) the moment the
fixes land, forcing the markers to be removed.

`Worker/test/unit/test_main.py`
1. `TestJobLoop::test_reserves_capacity_before_worker_thread_registers` (R1) — uses a
   real `JobExecutor` subclass whose `process_job` blocks *before* calling
   `_register_job`, and asserts `pull_job` is called at most `max_jobs` (=2) times.
   Fails today (8 calls) because `job_loop` does not reserve synchronously.
2. `TestJobLoop::test_resume_spawned_once_per_persisted_job` (R2) — uses a real executor
   subclass whose `resume_persisted_job_if_any` blocks before registering, with a
   persisted `j1`; asserts the resume target is started exactly once. Fails today (20
   starts) because `job_loop` re-spawns every poll.

`Worker/test/unit/test_executor.py`
3. `TestResumePersisted::test_malformed_entry_does_not_wipe_all_state` (R3) — feeds
   `[{no job_id}, {job_id: j2}]` and asserts `clear_running_job` is never called with
   `None` and is called with `"j2"`. Fails today (`clear_running_job(None)` fires first).

Notes on why the earlier tests could not catch these:
- `test_pulls_multiple_jobs_concurrently` and `test_respects_max_concurrent_jobs` mock
  `main.threading.Thread` / freeze `active_jobs_count`, so `process_job` never runs and
  the start→register window is invisible (R1).
- `test_resume_in_loop` only asserts the resume thread is created; it never asserts it is
  created once (R2).
- The persistence tests cover well-formed entries only (R3).

Existing tests that remain valid and unchanged: `test_effective_vram_passed_to_pull`,
`test_main_start_server_without_blocking_resume`, `TestFinalizeJob` (asserts
`clear_running_job("j")`), `test_success_resumes_releases_capacity`,
`test_pull_failure_releases_capacity`, `test_exception_releases_capacity`,
`test_multi_job_resume_*`, `has_unresumed_job` cases, all `TestJobState` multi-job /
migration / corrupt cases, and the `maxConcurrentJobs` round-trip.

---

## Part 4 — Remaining fix plan (source, not done here)

Ordered by blast radius:

1. **R3** — never clear-all from the resume loop for an entry without an id. Smallest,
   highest blast radius.
2. **R1** — add synchronous `reserve_job_slot`/`try_begin_job` and reserve in `job_loop`
   before `t.start()`; make `_register_job` idempotent.
3. **R2** — reserve/dedup resume (same reservation or a `_resuming` set) so
   `has_unresumed_job` is false from the moment a resume starts.
4. **R4** — remove the `None`-job_id log branches and make `job_id` mandatory.
5. **R5** — validate the persisted `job_id` before building image/output paths.
6. **R6** — drop the duplicate join, document the daemon-thread shutdown semantics.
7. Remove each `xfail` marker as its fix lands.

---

## Part 5 — Verification

From `Worker/`:

```bash
python -m pytest test/unit -q          # expect: N passed, 3 xfailed while R1-R3 are open
python -m pytest test/unit -q -rx      # shows the R1/R2/R3 reasons
```

When R1–R3 are fixed the three tests will XPASS; because they are `strict=True` this
fails the run and signals that the `xfail` markers must be deleted.

Manual smoke checks (unchanged):
- `MAX_CONCURRENT_JOBS=2`, submit two RUNNABLE jobs → both run concurrently; the
  heartbeat's `available_vram` drops by the sum of their `vram_required` before GPUtil
  reflects it; no more than two jobs ever run.
- Kill the worker mid-run with two jobs, restart → both are resumed once each (no
  duplicate containers) and the API server is reachable immediately.
- Finish one of two jobs → the other's `running_job.json` entry survives.
