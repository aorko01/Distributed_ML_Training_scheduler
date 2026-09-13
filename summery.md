# Distributed ML Training Scheduler — Summary

A distributed platform to submit, build, schedule, and monitor GPU ML jobs across many workers.

**Flow:** User uploads zipped code (`requirements.txt` required) → stored in `uploads` bucket → Builder builds `{user}/{job_id}:latest` and pushes to Docker Hub → VRAM estimation → scheduled to best-fit GPU worker → training with live logs and checkpoint uploads → `COMPLETED / FAILED / RETRY_NEEDED`.

## Components

**1. Scheduler `Scheduler/` (`:8000`)**
Central FastAPI orchestrator with Postgres + Redis.
- Job lifecycle with priorities `NORMAL / REQUESTED / HIGH` and pull-based scheduling: estimation job to max-free-VRAM worker, then retries, then largest-fitting training job (+1GB buffer).
- Stall watchdog requeues jobs if worker heartbeat lost >3min, tracks `gpu_hour` per user.
- Worker registry, cluster overview/throughput stats, resource marketplace, Redis-stream live logs (`GET` + `WS /jobs/{id}/logs/stream`), JWT auth, PyTorch tags helper.

**2. Docker Image Builder `Docker_Image_Builder/`**
Polls `unbuilt_jobs`, builds training images (`FROM base + COPY + pip install + CMD`) and interactive SSH sandboxes (Tailscale), pushes to Docker Hub, streams logs, saves `build.log`.

**3. Object Store `Object_store/` (`:8010`, MinIO `:9000/:9001`)**
S3-compatible storage for code (`uploads`) and outputs/logs (`outputs`). Supports direct upload/download and presigned URLs for large files.

**4. Worker `Worker/` (`:8600` telemetry)**
GPU host agent that registers, heartbeats every 5s, polls jobs every 10s.
- Hardware probing (GPU/VRAM/CPU/RAM/disk), persistent ID, crash resume via `running_job.json`.
- Runs VRAM probe (peak memory + step time), training in Docker `--gpus all` with output monitoring and checkpoint resume.
- Embedded API for live metrics, config edit, pause/resume.

**5. UIs `UI/`**
- **User:** submit jobs, track my jobs/GPU hours, live logs, machines, profile.
- **Admin:** cluster overview, nodes, job queue, throughput charts, users.
- **Worker (Electron desktop):** GPU/resource gauges, jobs table, logs, config, pause/resume.

## Stack & Run
`FastAPI, Postgres, Redis, Docker, MinIO, React+Vite, Electron` + Docker Hub, Tailscale SSH.
- Scheduler: `docker compose up` → `:8000/docs`
- Object Store: `docker compose up` → `:8010`
- Builder: `docker compose up`
- Worker: `pip install -r requirements.txt && python main.py`
- Each UI: `npm install && npm run dev`

---

## Feature-to-Code Map — read featurewise, not folder-wise

> How to use: pick a feature below, read files in listed order. `Scheduler/app/...`, `Worker/...`, `Docker_Image_Builder/...`, `Object_store/...`, `UI/...` paths are repo-relative.

### Quick index

| # | Feature | Start here → End here |
|---|---------|-----------------------|
| F1 | Auth + User profile | `Scheduler/app/api/auth_route.py` → `services/auth_service.py` → `utils/auth.py` → `UI/User/src/services/auth.ts` |
| F2 | Job submission + validation + upload | `UI/User/src/pages/SubmitJob.tsx` → `Scheduler/app/api/jobs_route.py:submit_job` → `utils/file_utils.py` → `Object_store/main.py:upload_object` |
| F3 | Docker image building (training + interactive) | `Docker_Image_Builder/builder.py:scan_and_process` → `docker_ops.py:build_push_and_clean` → `api.py` |
| F4 | VRAM estimation | `Worker/vram_estimation.py:estimate` → `Worker/executor.py:handle_vram_estimation` → `Scheduler/services/job_service.py:save_vram_estimation` |
| F5 | Scheduling / dispatch / priorities / resume | `Scheduler/services/job_service.py:get_next_job_for_worker` → `api/jobs_route.py:pull_job,resume_job` → `Worker/main.py:job_loop` |
| F6 | Training execution on worker | `Worker/executor.py:handle_training,_run_container` → `output_monitor.py` → `object_store.py` |
| F7 | Checkpoint resume + crash recovery | `Worker/job_state.py` + `executor.py:_resume_attempt,_restore_job_output` + `output_monitor.py:write_baseline/load_baseline` |
| F8 | Live logs + build logs + outputs | `Scheduler/services/log_service.py` → `api/jobs_route.py:ingest_job_logs,get_job_logs,job_logs_stream` → `Worker/executor.py:_flush_log_push` → `UI/User/src/components/LogTerminal.tsx` |
| F9 | Object Store (code/outputs/presigned) | `Object_store/main.py` + `init_buckets.py` → `Worker/object_store.py` |
| F10 | Worker registry + heartbeat + watchdog | `Worker/hardware.py:collect_node_info` → `Worker/api.py:register_worker,send_heartbeat` → `Scheduler/services/worker_service.py,watchdog_service.py` |
| F11 | Cluster stats / admin overview / throughput | `Scheduler/services/scheduler_service.py` → `api/scheduler_route.py` → `UI/Admin/src/pages/Overview.tsx` |
| F12 | Resource marketplace (request GPU) | `Scheduler/services/resource_service.py` → `api/resource_route.py` → `UI/User/src/pages/Machines.tsx` |
| F13 | PyTorch/CUDA image picker helper | `Scheduler/app/api/docker_route.py:get_pytorch_tags` → `UI/User/src/services/docker.ts` |
| F14 | Worker local dashboard (telemetry/config/pause) | `Worker/server.py,telemetry.py,runtime_config.py` → `UI/Worker/src/renderer/src/` |
| F15 | User UI + Admin UI shells | `UI/User/src/App.tsx`, `UI/Admin/src/App.tsx` |

---

### F1 — Auth + User profile

Read in order:

1. `Scheduler/app/models/user_model.py` — `User` (user_id, username, email, hashed_password, is_active, is_superuser)
2. `Scheduler/app/schemas/user_schema.py` — `UserCreate, UserLogin, UserUpdate, UserResponse, Token, TokenData`
3. `Scheduler/app/utils/auth.py` — `verify_password(), get_password_hash(), create_access_token(), decode_token()`
4. `Scheduler/app/services/auth_service.py` — `create_user(), get_user_by_username(), get_user_by_email(), get_user_by_id(), authenticate_user(), create_user_token(), update_user_profile()`
5. `Scheduler/app/api/auth_route.py` — `POST /auth/register→register(), POST /auth/login→login(), GET /auth/me→read_users_me(), PATCH /auth/me→update_profile()`
6. `Scheduler/app/api/deps.py` — `get_db(), get_current_user(), get_current_active_user()` + `oauth2_scheme`
7. `UI/User/src/services/api.ts` — `getToken/setToken/clearToken, request(), api.get/post/patch` (Bearer + 401 redirect)
8. `UI/User/src/services/auth.ts` — `login(), register(), logout(), getProfile(), updateProfile(), isAuthenticated()`
9. `UI/User/src/pages/Login.tsx:Login()`, `Register.tsx:Register()`, `Profile.tsx:Profile()`

Tests: `Scheduler/tests/test_auth_service.py`, `test_auth_utils.py`

### F2 — Job submission + validation + upload

Read in order:

1. `UI/User/src/pages/SubmitJob.tsx:SubmitJob()` — PyTorch/CUDA select (`handlePyTorchChange`), zip pick (`handleFileChange`), `bashScript/resumeCommand`, priority checkbox, `handleSubmit→submitJob→navigate(/jobs/:id)`
2. `UI/User/src/services/jobs.ts:submitJob()` — `FormData(zip_file,name,command,resume_command,docker_base_image,request_for_priority,reason_for_priority)` → `POST /jobs/submit_job`
3. `Scheduler/app/schemas/job_schema.py` — `JobCreate, JobResponse, JobResumeRequest, VramEstimationReport, JobFailureReport, InteractiveBuildRequest`
4. `Scheduler/app/models/job_model.py` — `JobStatus (NOT_RUNNABLE,VRAM_ESTIMATION_PENDING,RUNNABLE,IN_PROGRESS,COMPLETED,FAILED,RETRY_NEEDED,INTERACTIVE_READY)`, `JobPriority (NORMAL,REQUESTED,HIGH)`, `Job`
5. `Scheduler/app/utils/file_utils.py` — `find_file_in_zip(), validate_required_files(), save_to_object_store()` (requires `requirements.txt`, pushes zip to `uploads` bucket)
6. `Scheduler/app/api/jobs_route.py` — `POST /jobs/submit_job→submit_job(), POST /jobs/submit_interactive→submit_interactive(), GET /jobs/unbuilt_jobs→get_unbuilt_jobs(), GET /jobs/mine→get_my_jobs(), GET /jobs/mine/count, GET /jobs/mine/gpu_hours, GET /jobs/queue_length, GET /jobs/{job_id}→get_job_by_id()`
7. `Scheduler/app/services/job_service.py` — `create_job(), create_interactive_job(), mark_interactive_ready(), get_user_jobs(), get_user_jobs_count(), get_user_gpu_hours(), get_user_job_by_id(), get_runnable_jobs_count()`

Tests: `Scheduler/tests/test_job_service.py:TestCreateJob,TestInteractiveJobs`, `test_file_utils.py`, `test_api_routes.py:TestJobsRoutes`

### F3 — Docker image building (training + interactive SSH)

Read in order:

1. `Docker_Image_Builder/config.py` — constants only: `SCHEDULER_*_URL, OBJECT_STORE_URL/BUCKET/OUTPUT_BUCKET, DOCKER_HUB_USERNAME, POLL_INTERVAL, DB_PATH`
2. `Docker_Image_Builder/database.py` — `init_db(), is_job_processed(), mark_job_processed(), update_base_image_usage(), get_old_base_images(), remove_base_image_record()` (SQLite idempotency + base-image LRU)
3. `Docker_Image_Builder/api.py` — `fetch_unbuilt_jobs()` (`GET /jobs/unbuilt_jobs`), `download_job_archive()` (`GET /objects/{BUCKET}/{key}`), `send_log_lines()` (`POST /jobs/logs/{id}`), `notify_scheduler_job_ready()` (`POST /jobs/update_job_to_vram_estimation_pending`), `notify_scheduler_interactive_ready()` (`POST /jobs/mark_interactive_ready`), `notify_scheduler_job_failed()` (`POST /jobs/mark_failed`)
4. `Docker_Image_Builder/docker_ops.py` —
   - Build: `docker_login(), generate_dockerfile(), generate_interactive_dockerfile(), generate_interactive_entrypoint(), build_push_and_clean()` (tags `{USER}/{job_id}:latest` vs `{USER}/{job_id}-interactive:latest`, `client.images.build/push/remove`)
   - Logs: `emit_build_lines(), _extract_build_log_lines(), should_upload_build_line(), maybe_upload_build_logs() (60s throttle), upload_build_logs()` (`POST /objects/upload` → `{job_id}/build.log`), `save_debug_copy(), prune_old_base_images()`
5. `Docker_Image_Builder/builder.py` — `find_project_dir(), extract_job_archive(), scan_and_process()` (poll → validate `id/object_key/docker_base_image/build_type` → training vs interactive → notify ready/failed), `main()` (poll loop `sleep(POLL_INTERVAL)`)

Tests: `Docker_Image_Builder/tests/test_builder.py, test_docker_ops.py, test_api.py, test_database.py, test_config.py`

### F4 — VRAM estimation (peak memory + step time probe)

Read in order:

1. `Worker/vram_estimation.py` — `estimate(target,target_args)` (monkey-patches `torch.optim.Optimizer.__init__/step` via `optimizer_init/step_wrapper`, raises `ProbeDone`), `main()` (`--output,--target,--target_args`)
2. `Worker/config.py` — `VRAM_ESTIMATION_SCRIPT`, `OBJECT_STORE_URL, OBJECT_OUTPUT_BUCKET`
3. `Worker/executor.py` — `handle_vram_estimation()` + `_parse_python_command()` (+ `SchedulerAPI.save_vram_estimation`)
4. `Scheduler/app/api/jobs_route.py` — `POST /jobs/update_job_to_vram_estimation_pending→update_job_to_vram_estimation_pending(), POST /jobs/save_vram_estimation→save_vram_estimation()`
5. `Scheduler/app/services/job_service.py` — `set_job_vram_estimation_pending(), save_vram_estimation(), set_job_runnable(), _check_vram_estimation_strategy(), _is_highest_vram_worker(), _get_connected_workers_vram()`
6. `UI/User/src/services/jobs.ts:mapStatus()` (Pending/Building mapping) + `UI/User/src/pages/Dashboard.tsx:getStatusBadge()`

Tests: `Worker/tests/test_vram_estimation.py`, `Scheduler/tests/test_job_service.py:TestVramEstimationStrategy,TestStateTransitions`

### F5 — Scheduling / dispatch / priorities / retry / resume

Read in order:

1. `Scheduler/app/services/job_service.py` — core: `SCHEDULING_STRATEGIES=[_check_vram_estimation_strategy,_check_retry_job_strategy,_check_training_job_strategy]`, `get_next_job_for_worker()` (estimation → max-free-VRAM worker; then retries; then largest-fitting training job +1GB buffer), `_format_job_response(), get_job_for_resume(), mark_job_failed(), set_to_completed()`, `JOB_WORKER_KEY_PREFIX, TERMINAL_STATUSES`
2. `Scheduler/app/api/jobs_route.py` — `POST /jobs/pull_job→pull_job(), POST /jobs/resume→resume_job(), POST /jobs/update_job_to_runnable→update_job_to_runnable(), POST /jobs/mark_completed→mark_job_completed(), POST /jobs/mark_failed→mark_job_failed()`
3. `Worker/api.py:SchedulerAPI` — `pull_job(), resume_job(), mark_job_completed(), mark_job_failed()`
4. `Worker/main.py` — `job_loop()` (poll every 10s → `process_job`), `heartbeat_loop()` (5s), `main()`
5. `Worker/executor.py` — `process_job(), handle_retry(), handle_training(), _finalize_job()`
6. `UI/Admin/src/pages/JobQueue.tsx:JobQueue()` — `move(±1), decidePriority(approved→high), getPriorityBadge/getRequestBadge` (currently mock `data/mock.ts:queueJobs`)
7. `UI/User/src/pages/Dashboard.tsx:Dashboard()` — filter/sort `visibleJobs`, `getStatusBadge, formatDate`

Tests: `Scheduler/tests/test_scheduler_service.py` no — scheduling is `test_job_service.py:TestTrainingAndRetryStrategies,TestGetNextJobForWorker,TestGetJobForResume,TestCompletionAndFailure`

### F6 — Training execution on worker (Docker `--gpus all`)

Read in order:

1. `Worker/executor.py:JobExecutor` — `__init__, pull_docker_image(), _image_workdir(), _resolve_mount_target(), _prepare_output_mount(), _container_user_args(), _parse_python_command(), handle_training(), _run_container(), _remove_output_dir(), _finalize_job(), _flush_log_push(), _append_build_log(), process_job(), resume_persisted_job_if_any()`, helper `_docker_host_path(), _record_job()`
2. `Worker/output_monitor.py:OutputFileMonitor` — `run/stop/flush/_scan/pending_uploads/_maybe_upload` (thread watching output dir → Object Store)
3. `Worker/object_store.py:ObjectStore` — `upload_bytes(), upload_file(), _presign_upload(), _upload_large(), download(), list_objects(), _presign_download(), download_to()` + `_post_retry()`
4. `Worker/api.py:SchedulerAPI.send_logs(), mark_job_completed(), mark_job_failed()`
5. `Worker/job_state.py` — `save_running_job(), load_running_job(), clear_running_job()` (`running_job.json`)

Tests: `Worker/tests/test_executor.py`, `test_output_monitor.py`, `test_object_store.py`, `test_main.py`

### F7 — Checkpoint resume + crash recovery

Read in order:

1. `Worker/job_state.py` — `load_running_job(), save_running_job(job_id), clear_running_job()`
2. `Worker/output_monitor.py` — `write_baseline(), load_baseline()` (`META_FILE=.output_baseline`)
3. `Worker/executor.py` — `_resume_attempt(), _restore_job_output(), handle_retry(), resume_persisted_job_if_any()`
4. `Worker/api.py:SchedulerAPI.resume_job()` ↔ `Scheduler/app/api/jobs_route.py:POST /jobs/resume→resume_job()` ↔ `Scheduler/app/services/job_service.py:get_job_for_resume()`
5. `Worker/main.py:main()` — calls resume-on-boot before loops

Tests: `Worker/tests/test_state_config_telemetry_io.py:TestJobState`, `test_executor.py:TestHandleRetry,TestResumeAttempt,TestRestoreJobOutput,TestResumePersisted`

### F8 — Live logs + build logs + outputs download

Read in order:

1. `Scheduler/app/schemas/log_schema.py:LogLinesRequest`
2. `Scheduler/app/services/log_service.py` — `_stream_key(), publish_log_lines(), get_log_stream_history(), read_log_stream(), fetch_build_log_from_object_store()` (`LOG_STREAM_PREFIX=logs:, LOG_STREAM_MAXLEN=10000`, Redis streams)
3. `Scheduler/app/api/jobs_route.py` — `POST /jobs/logs/{job_id}→ingest_job_logs(), GET /jobs/{job_id}/logs→get_job_logs(), WS /jobs/{job_id}/logs/stream→job_logs_stream()` + `_ws_authenticate()`, plus `POST /jobs/upload_output→upload_output_file(), POST /jobs/get_output_by_id→get_output_by_id()`
4. `Scheduler/app/core/redis.py` — `redis_client`
5. `Worker/executor.py` — `_flush_log_push(), _append_build_log()`; `Worker/api.py:send_logs()`; `Docker_Image_Builder/docker_ops.py:emit_build_lines(), upload_build_logs()` + `api.py:send_log_lines()`
6. `UI/User/src/services/jobs.ts` — `fetchJobLogs(), streamJobLogs() (WS ?token&after, init/log/done, reconnect 2s), classifyLogLine(), toLogLine(), mergeWithStored()`
7. `UI/User/src/components/LogTerminal.tsx:LogTerminal()` — `handleScroll, getLogClass, formatTime`
8. `UI/User/src/pages/JobDetails.tsx:JobDetails()` — live vs history switch, `handleDownloadOutput` (client zip: `createZipBlob, crc32, utf8Bytes, writeUint16LE/32LE`)

Tests: `Scheduler/tests/test_log_service.py`, `Worker/tests/test_api.py:TestSendLogs`

### F9 — Object Store (code + outputs + presigned URLs)

Read in order:

1. `Object_store/main.py` — `get_client(), get_public_client(), lifespan()` + `GET /health→health(), POST /objects/upload→upload_object(), POST /objects/presign_upload→presign_upload(), GET /objects/list→list_objects(), POST /objects/presign_download→presign_download(), GET /objects/{bucket}/{key}→download_object()+iter_response()`
2. `Object_store/init_buckets.py` — `get_client(), ensure_buckets()` (`uploads, outputs`)
3. `Worker/object_store.py:ObjectStore` (see F6) + `Worker/config.py:OBJECT_STORE_URL, OBJECT_OUTPUT_BUCKET, OBJECT_STORE_LARGE_FILE_THRESHOLD`
4. `Docker_Image_Builder/api.py:download_job_archive()` + `docker_ops.py:upload_build_logs()`
5. `Scheduler/app/utils/file_utils.py:save_to_object_store()` + `Scheduler/app/services/log_service.py:fetch_build_log_from_object_store()`

### F10 — Worker registry + heartbeat + stall watchdog

Read in order:

1. `Worker/hardware.py` — `get_or_create_worker_id(), get_hostname(), get_ip_address(), get_cpu_load/cores, get_mem_usage/total_gb, get_gpu_info(), count_gpus_in_use(), get_gpus_info(), get_gpu_temperature(), get_disk_info(), docker_available(), cuda_available(), get_os_info(), collect_node_info()`
2. `Worker/api.py:SchedulerAPI.register_worker(), send_heartbeat()` + `Worker/config.py:get_scheduler_url(), set_scheduler_url(), persist_env()` + `Worker/main.py:heartbeat_loop()`
3. `Scheduler/app/models/worker_model.py:Worker` + `schemas/worker_schema.py:WorkerInfo,WorkerResponse,WorkerResource,WorkerNodeInfo` + `schemas/heartbeat_schema.py:HeartbeatSchema,HeartbeatResponse`
4. `Scheduler/app/services/worker_service.py` — `register_or_update_worker_service(), process_heartbeat(), _update_redis_worker(), get_last_heartbeat(), _update_db_worker_metrics(), get_all_workers(), get_total_gpus()`, `_apply_worker_metrics()`
5. `Scheduler/app/api/worker_route.py` — `POST /workers/register→register_worker(), POST /workers/heartbeat→worker_heartbeat(), GET /workers/total_gpus, GET /workers/nodes→get_nodes()`
6. `Scheduler/app/services/watchdog_service.py` — `_last_heartbeat_ts(), _mark_retry_needed(), _cleanup_stale_job_workers(), check_stalled_jobs(), run_stall_watcher()` (`STALL_TIMEOUT=180s, SCAN_INTERVAL=30s`, `RUNNING={IN_PROGRESS,VRAM_ESTIMATION_PENDING}`); started in `Scheduler/app/main.py:lifespan()` + `app/db/database.py:run_migrations()`
7. `UI/User/src/services/workers.ts:fetchAllNodes()` + `UI/Admin/src/services/api.ts:fetchNodes()` → `UI/Admin/src/pages/Nodes.tsx:Nodes()`

Tests: `Scheduler/tests/test_worker_service.py, test_watchdog_service.py`, `Worker/tests/test_hardware.py, test_api.py:TestRegisterWorker,TestSendHeartbeat`

### F11 — Cluster stats / admin overview / throughput

Read in order:

1. `Scheduler/app/services/scheduler_service.py` — `get_overview(), get_throughput(), _completion_time(), _bucket_daily/weekly/monthly/yearly()`
2. `Scheduler/app/api/scheduler_route.py` — `GET /scheduler/health→health_check(), GET /scheduler/overview→cluster_overview(), GET /scheduler/throughput→job_throughput()`
3. `UI/Admin/src/services/api.ts` — `fetchOverview(), fetchThroughput(), fetchNodes()`
4. `UI/Admin/src/pages/Overview.tsx:Overview()` — poll 5s, `RingChart(Resource Distribution) + BarChart(Job Throughput)`, metric cards
5. `UI/Admin/src/components/RingChart.tsx:RingChart()`, `BarChart.tsx:BarChart()`
6. `UI/User/src/services/stats.ts:fetchClusterStats()` (`/jobs/queue_length,/jobs/mine/count,/jobs/mine/gpu_hours,/workers/total_gpus`) → `UI/User/src/pages/Dashboard.tsx`

Tests: `Scheduler/tests/test_scheduler_service.py`

### F12 — Resource marketplace (request / match GPU)

Read in order:

1. `Scheduler/app/models/resource_request_model.py:ResourceRequest` + `schemas/resource_schema.py:ResourceOptions,ResourceConfig,ResourceSummaryResponse,ResourceRequestCreate,ResourceRequestResponse`
2. `Scheduler/app/services/resource_service.py` — `get_resource_options(), get_resource_summary(), create_resource_request(), _config_matches(), _compare(), _int_or_float()`
3. `Scheduler/app/api/resource_route.py` — `GET /resources/options, POST /resources/summary→get_resource_summary(), POST /resources/request→create_resource_request()`
4. `UI/User/src/services/workers.ts` — `fetchResourceOptions(), fetchResourceSummary(), createResourceRequest(), fetchAllNodes(), mapNode()`
5. `UI/User/src/pages/Machines.tsx:Machines()` — `fetchAllNodes+fetchResourceOptions, fetchResourceSummary, numMatch, visibleNodes, handleAcquire→createResourceRequest`, sub `ResourceBar, statusBadge`

Tests: `Scheduler/tests/test_resource_service.py`

### F13 — PyTorch / CUDA image picker helper

Read in order:

1. `Scheduler/app/api/docker_route.py` — `GET /docker/pytorch-tags→get_pytorch_tags(), _fetch_all_tags(), _parse_runtime_tags(), _sort_key()` (Docker Hub `pytorch/pytorch` runtime tags)
2. `UI/User/src/services/docker.ts:fetchPytorchVersions()` (`CudaVariant, PytorchVersion`)
3. `UI/User/src/pages/SubmitJob.tsx` — consumes versions for `docker_base_image`

Tests: `Scheduler/tests/test_docker_route.py`

### F14 — Worker local dashboard (telemetry API + Electron UI + config/pause)

Read in order:

1. `Worker/telemetry.py` — `_append_event_locked(), record_job(), record_event(), record_heartbeat(), set_paused(), is_paused(), get_jobs(), get_events(), get_heartbeat()`
2. `Worker/runtime_config.py` — `get(), set_many(), all()`
3. `Worker/io_monitor.py:IORateMonitor.sample()` (+ `io_monitor` singleton)
4. `Worker/server.py` — models `WorkerInfo,Metrics,GpuInfo,JobRecord,EventRecord,ConfigState,Status,ConfigUpdate` + `GET /health→health, GET /api/worker, /api/metrics, /api/gpus, /api/jobs, /api/events, /api/status, /api/config, PUT /api/config→update_config, POST /api/control/pause→pause, /resume→resume, WS /ws/metrics→ws_metrics, run(), run_in_thread()`, helpers `_build_worker_info/_metrics/_status, _dump()`
5. `UI/Worker/src/renderer/src/api/worker.ts` — `fetchWorker/fetchMetrics/fetchGpus/fetchJobs/fetchEvents/fetchStatus/fetchConfig/updateConfig/pauseWorker/resumeWorker()` (`:8600`, `WS /ws/metrics`)
6. `UI/Worker/src/renderer/src/hooks/useWorkerData.ts:useWorkerData()` (WS live + 2.5s poll fallback, `HISTORY_LEN=48`)
7. `UI/Worker/src/renderer/src/views/Dashboard.tsx:Dashboard()` + `components/TopBar, Sidebar, StatCard, InfoCard, GpuCard, JobsTable, Sparkline, RingGauge, ConfigModal` + `types.ts:formatDuration,formatBytes`
8. `UI/Worker/src/main/index.ts:createWindow()` + `preload/index.ts` (`window.worker.workerApiUrl=127.0.0.1:8600`)

Tests: `Worker/tests/test_server.py, test_state_config_telemetry_io.py`

### F15 — App shells / routing (User, Admin, Worker)

- **User (`UI/User/` React+Vite):** `src/App.tsx:App()+ProtectedRoute` (`/login,/register,/→Layout→Dashboard,SubmitJob,Machines,Jobs/:id,Profile`), `src/main.tsx`, `components/Layout.tsx:Layout()+handleLogout`
- **Admin (`UI/Admin/` React+Vite):** `src/App.tsx:App()+ProtectedRoute` (`/login,/→Layout→Overview,Nodes,Users,JobQueue`), `components/Layout.tsx`, `services/auth.ts` (dummy `admin/admin`), `data/mock.ts` (seeds `clusterOverview,nodes,throughput,users,queueJobs`), `pages/Users.tsx:Users() (toggleStatus,promote,deleteUser)`, `pages/Nodes.tsx:Nodes() (toClusterNode,handleDisconnect,handleSsh)`, `pages/Login.tsx`
- **Worker (`UI/Worker/` Electron+React):** `src/main/index.ts`, `src/preload/index.ts/index.d.ts`, `electron.vite.config.ts`, `src/renderer/src/App.tsx:App(), main.tsx`
- **Infra bootstraps:** `Scheduler/app/main.py:lifespan()` (mounts `jobs,scheduler,workers,auth,docker,resources` routers + `run_stall_watcher`), `Scheduler/app/db/database.py:run_migrations()`, `Scheduler/docker-compose.yml + Dockerfile + run.sh`, `Object_store/docker-compose.yml + Dockerfile`, `Docker_Image_Builder/docker-compose.yml + Dockerfile`, `Worker/.env + requirements.txt + run.bat`
