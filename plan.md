Goal
Add threading to the Docker_Image_Builder so it can build three images concurrently, with atomic job claiming via a new IMAGE_BUILDING status, and automatic re-release of system-failed jobs back to NOT_RUNNABLE.
Files to touch
1. Scheduler/app/models/job_model.py — add IMAGE_BUILDING to JobStatus enum
2. Scheduler/app/services/job_service.py — add claim_job_for_building() and release_job_to_not_runnable() service functions
3. Scheduler/app/api/jobs_route.py — add POST /jobs/claim_for_building and POST /jobs/release_to_not_runnable endpoints
4. Docker_Image_Builder/config.py — add MAX_CONCURRENT_BUILDS env var
5. Docker_Image_Builder/api.py — add claim_job_for_building() and release_job_to_not_runnable() HTTP functions
6. Docker_Image_Builder/builder.py — replace sequential scan_and_process() with threaded worker_loop() using ThreadPoolExecutor(max_workers=3)
7. Docker_Image_Builder/test/unit/test_builder.py — update TestScanAndProcess and TestMain to test the new threaded architecture
8. Docker_Image_Builder/test/unit/test_api.py — add tests for the two new API functions
Steps
Step 1: Add IMAGE_BUILDING to JobStatus enum
File: Scheduler/app/models/job_model.py
Insert IMAGE_BUILDING = "IMAGE_BUILDING" between IN_PROGRESS and COMPLETED in the JobStatus enum. This is the state a job enters when a builder thread claims it, preventing other threads/instances from pulling the same job.
Step 2: Add claim_job_for_building() and release_job_to_not_runnable() to job_service.py
File: Scheduler/app/services/job_service.py
Add two new functions:
- claim_job_for_building(db: Session) -> dict | None  
Atomically finds the oldest NOT_RUNNABLE job, sets its status to IMAGE_BUILDING, commits, and returns the job dict (using the existing _format_job_response helper with flag "image_building"). Returns None if no job is available.  
Rationale for atomicity: SQLite serializes write transactions, so a simple query-then-update inside a single commit is safe. For PostgreSQL, a with_for_update(skip_locked=True) can be added later.
- release_job_to_not_runnable(db: Session, job_id: str) -> Job  
Finds the job by ID, verifies its status is IMAGE_BUILDING, sets it back to NOT_RUNNABLE, commits, and returns the job. Raises an exception if the job is not found or not in IMAGE_BUILDING state.
Step 3: Add two new endpoints to jobs_route.py
File: Scheduler/app/api/jobs_route.py
- POST /jobs/claim_for_building — calls job_service.claim_job_for_building(db) and returns the result. If None, returns {"message": "No unbuilt jobs available"}.
- POST /jobs/release_to_not_runnable — accepts a JobIDRequest body, calls job_service.release_job_to_not_runnable(db, request.job_id), returns {"job_id": ..., "status": ...}.
No new Pydantic schemas are needed — JobIDRequest already exists.
Step 4: Add MAX_CONCURRENT_BUILDS to config.py
File: Docker_Image_Builder/config.py
Add:
MAX_CONCURRENT_BUILDS = int(os.environ.get("MAX_CONCURRENT_BUILDS", "3"))
Step 5: Add claim_job_for_building() and release_job_to_not_runnable() to api.py
File: Docker_Image_Builder/api.py
Add two new functions:
- claim_job_for_building() -> dict | None  
POSTs to SCHEDULER_BASE_URL + "/jobs/claim_for_building" with timeout 10. Returns the JSON body on 200, or None if the response contains "message": "No unbuilt jobs available" or any error.
- release_job_to_not_runnable(job_id: str) -> bool  
POSTs {"job_id": job_id} to SCHEDULER_BASE_URL + "/jobs/release_to_not_runnable" with timeout 10. Returns True on 200 (no error in body), False otherwise.
Step 6: Rewrite builder.py with threading
File: Docker_Image_Builder/builder.py
Replace the sequential scan_and_process() with a threaded architecture:
1. Remove scan_and_process() entirely (its logic moves into worker_loop).
2. Add worker_loop(client: docker.DockerClient) — each thread runs this indefinitely:
- Calls claim_job_for_building() to atomically claim one NOT_RUNNABLE job.
- If no job available, sleep(POLL_INTERVAL) and retry.
- If the claimed job is malformed (missing id/object_key/base_image), calls release_job_to_not_runnable(job_id) and continues.
- Otherwise, processes the job identically to the old scan_and_process body: download archive → extract → find project dir → build_push_and_clean.
- On success (result is None): calls notify_scheduler_job_ready(job_id).
- On "user" failure: calls notify_scheduler_job_failed(job_id, "user", reason).
- On "system" failure: calls release_job_to_not_runnable(job_id) so another builder thread/instance can retry.
- Wraps the entire loop body in a try/except so unexpected exceptions are logged and the thread continues.
3. Rewrite main():
- Keep the existing setup (init_db, docker.from_env, docker_login, prune_old_base_images).
- Replace the while True: scan_and_process(); sleep(POLL_INTERVAL) loop with:
with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_BUILDS) as executor:
    futures = [executor.submit(worker_loop, client) for _ in range(MAX_CONCURRENT_BUILDS)]
    # Main thread stays alive to handle periodic pruning
    last_prune_time = time.monotonic()
    PRUNE_INTERVAL = 86400
    while True:
        try:
            now = time.monotonic()
            if now - last_prune_time >= PRUNE_INTERVAL:
                prune_old_base_images(client)
                last_prune_time = now
        except Exception as e:
            logger.error("Error during prune cycle: %s", e, exc_info=True)
        time.sleep(60)
- Import ThreadPoolExecutor from concurrent.futures.
Step 7: Update unit tests in test_builder.py
File: Docker_Image_Builder/test/unit/test_builder.py
Justification for changes: The old TestScanAndProcess and TestMain classes test the sequential scan_and_process() function and the old main() loop. Since both are being replaced by the threaded worker_loop() and the new main(), these tests must be updated to cover the new code paths. The utility tests (TestFindProjectDir, TestExtractJobArchive) remain unchanged.
- Replace TestScanAndProcess with TestWorkerLoop:
- test_fetch_failure_returns_quietly — mock claim_job_for_building to raise an exception; verify the thread doesn't crash.
- test_no_jobs_sleeps — mock claim_job_for_building to return None; verify it sleeps and retries (use side_effect=[None, None, None] and a sentinel to break the loop).
- test_training_success — mock claim_job_for_building to return a job dict, mock download_job_archive, extract_job_archive, find_project_dir, build_push_and_clean (returns None), notify_scheduler_job_ready (returns True); verify the job is processed and notified.
- test_user_failure_reported — same setup but build_push_and_clean returns ("user", "pip failed"); verify notify_scheduler_job_failed is called.
- test_system_failure_released — same setup but build_push_and_clean returns ("system", "daemon down"); verify release_job_to_not_runnable is called (not notify_scheduler_job_failed).
- test_download_exception_becomes_system_result — download_job_archive raises; verify release_job_to_not_runnable is called.
- Update TestMain:
- test_main_loops_and_sleeps — mock init_db, docker, docker_login, prune_old_base_images, worker_loop (side_effect to raise KeyboardInterrupt after a short sleep), and ThreadPoolExecutor so it doesn't actually spawn threads. Verify init_db, docker_login, prune_old_base_images are called.
- test_main_survives_prune_errors — similar setup but mock prune_old_base_images to raise once then succeed.
Step 8: Add tests for new API functions in test_api.py
File: Docker_Image_Builder/test/unit/test_api.py
Justification: New functions need test coverage. This is not "making the task easier" — it's ensuring the new claim/release HTTP calls work correctly.
Add two new test classes:
- TestClaimJobForBuilding:
- test_returns_job_on_success — mock requests.post to return 200 with a job dict; verify the job dict is returned.
- test_no_jobs_returns_none — mock requests.post to return 200 with {"message": "No unbuilt jobs available"}; verify None is returned.
- test_error_returns_none — mock requests.post to return 500; verify None is returned.
- test_exception_returns_none — mock requests.post to raise; verify None is returned.
- TestReleaseJobToNotRunnable:
- test_success_returns_true — mock requests.post to return 200 with {"status": "NOT_RUNNABLE"}; verify True.
- test_error_returns_false — mock requests.post to return 500; verify False.
- test_exception_returns_false — mock requests.post to raise; verify False.
Risks / open questions
1. Database atomicity across PostgreSQL vs SQLite: The simple query-then-update in claim_job_for_building is safe for SQLite (serialized writes) but could race under high-concurrency PostgreSQL. If the production DB is PostgreSQL, with_for_update(skip_locked=True) should be added. This can be done as a follow-up.
2. Thread safety of docker.DockerClient: The Docker SDK's DockerClient is generally safe to use from multiple threads for independent build operations (each build creates its own temp dirs and image tags). No shared mutable state is accessed across threads except _logged_in in docker_ops.py — this global is only written during login/prune (which happens before threads start) and during push auth-error recovery (which is per-job and thread-local in practice). If a race on _logged_in becomes an issue, it can be converted to a threading.Lock later.
3. Pruning race: The main thread calls prune_old_base_images while worker threads may be using base images. The prune function already catches docker.errors.ImageNotFound and generic exceptions, so a concurrent build using a base image that gets pruned will fail with a system error and be re-released to NOT_RUNNABLE — acceptable.
4. Thread exit on unrecoverable errors: If a thread encounters a fatal error (e.g., Docker daemon permanently down), it will log and sleep-loop. No automatic thread restart is implemented; a container restart would be needed. This matches the existing behavior where the whole process would loop on errors.
5. No changes to e2e tests per user instruction.
Done criteria
1. All existing unit tests pass (after updating test_builder.py and test_api.py):
cd Docker_Image_Builder && python -m pytest test/unit/ -v
2. New tests for claim_job_for_building, release_job_to_not_runnable, and worker_loop pass.
3. The Scheduler starts without errors and the new endpoints respond correctly:
curl -X POST http://localhost:8000/jobs/claim_for_building
curl -X POST http://localhost:8000/jobs/release_to_not_runnable \
  -H "Content-Type: application/json" -d '{"job_id": "some-id"}'
4. The Docker_Image_Builder starts and shows three worker threads in the logs:
[INFO] Docker Image Builder service starting ...
[INFO] Starting 3 concurrent builder threads
5. When a job is claimed, its status in the database changes to IMAGE_BUILDING and it no longer appears in the GET /jobs/unbuilt_jobs response.
6. When a build fails with a system error, the job's status returns to NOT_RUNNABLE and reappears in GET /jobs/unbuilt_jobs.