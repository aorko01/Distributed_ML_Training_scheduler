Final Verification & Edge Case Fixes for Docker Image Builder & Scheduler
User Review Required
IMPORTANT

The updates you made to the unstaged files have successfully resolved all 6 major concurrency and lifecycle issues! Both test suites pass completely (135 tests in Scheduler, 100 tests in Docker Image Builder).

Only 2 minor edge cases remain to make the system 100% resilient:

Premature Deletion & Unhandled Failure on Notification Timeout (builder.py:177-183): In _push_and_notify, delete_local_image was called outside if notified:. If scheduler notification times out or fails after a successful push, the local image is deleted anyway, and notify_scheduler_job_failed is not called, leaving the job stranded in PENDING.
Derived Interactive Notification Failure (builder.py:115): If notify_scheduler_interactive_ready returns False, notify_scheduler_job_failed is not called, leaving the job stranded in PENDING.
Verification Summary of Unstaged Changes
Component	Status	Verification Detail
Active Job Tracking	✅ Verified	_claim_job and _release_job track jobs through both build and push. Released properly in finally.
Early Returns in process_job	✅ Verified	Malformed jobs and already-processed jobs call _release_job(job_id) before exiting.
Exception-Safe Push Cleanup	✅ Verified	_push_and_notify wraps execution in try...finally: _release_job(job_id).
System Failures on PENDING Jobs	✅ Verified	job_service.mark_job_failed resets PENDING jobs to NOT_RUNNABLE on system failure.
Watchdog updated_at Fallback	✅ Verified	check_stalled_pending_jobs falls back to job.created_at when job.updated_at is None.
Base Image In-Flight Tracking	✅ Verified	_in_flight_base_images prevents prune_old_base_images from deleting active base images.
Concurrent Access Image Pulls	✅ Verified	_access_image_lock serializes access image pulls across threads.
Database Concurrency	✅ Verified	SQLite WAL mode and write lock serialized.
Remaining 2 Edge Cases to Fix
1. Guard delete_local_image and report failure on notification timeout
In Docker_Image_Builder/builder.py:

python

if build_type == "interactive":
    notified = notify_scheduler_interactive_ready(job_id)
else:
    notified = notify_scheduler_job_ready(job_id)
if notified:
    mark_job_processed(job_id)
    logger.info("Job %s completed.", job_id)
    delete_executor.submit(delete_local_image, client, job_id, image_tag_str)
else:
    logger.error("Job %s built but scheduler notification failed, will retry.", job_id)
    notify_scheduler_job_failed(job_id, "system", "Scheduler notification failed after build")
2. Handle derived interactive notification failure
In Docker_Image_Builder/builder.py:

python

notified = notify_scheduler_interactive_ready(job_id)
if notified:
    mark_job_processed(job_id)
    logger.info("Job %s completed (derived interactive).", job_id)
else:
    logger.error("Job %s notification failed (derived interactive).", job_id)
    notify_scheduler_job_failed(job_id, "system", "Derived interactive notification failed")
return
3. Protect docker_login with _login_lock
In Docker_Image_Builder/docker_ops.py: Add _login_lock = threading.Lock() around check and login to ensure thread-safe authentication.

Verification Plan
Automated Tests
Run full test suite:

bash

pytest Docker_Image_Builder/tests -v
pytest Scheduler/tests -v
Assert 100% tests pass with no regressions.