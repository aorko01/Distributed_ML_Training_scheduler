# Interactive runtime implementation handoff

This phase implements durable machine reservations, versioned authenticated Worker
execution, runtime lifecycle/controller APIs, real Docker-exec broker, Start/Stop
and a browser connection verification. Admission is disabled by default. There is
no editor, snapshot, image commit or Save implementation.

## Changed areas

- `Scheduler/app/models/interactive_runtime_model.py`, `models/worker_model.py`,
  `migrations/002_interactive_runtimes.sql`, `db/database.py`: protected Worker
  metadata, immutable source-pinned runtime/generation, durable assignment rows,
  partial uniqueness, XOR target constraints, request keys and cleanup tombstones;
  ordered migration execution under the existing advisory lock.
- `Scheduler/app/services/scheduling/{types,config,policy,claims}.py`: selectable
  policy factory, estimation -> compatible whole-machine interactive -> batch
  retry/training order, unavailable-Worker VRAM exclusion, Worker-row serialization,
  request replay, generation/instance/token fences, results held until cleanup,
  lease expiry quarantine and exact release.
- Scheduler runtime service/controller/management client, Worker execution and
  owner runtime routers, strict schemas, `app/main.py`: Start/current status/Stop/
  no-store grants, protected per-Worker service authentication and rotation,
  bounded messages, heartbeat lease/Stop instructions, persisted reconciler leases,
  management exact enrollment replay/confirmation, health/probe readiness, exact
  revocation and late side-effect compensation.
- Legacy job/Worker services/routes and watchdog: old placement/resume routes
  fail closed; assignment-managed result/output callbacks require the new fence;
  unauthenticated legacy telemetry cannot change protected Worker state; Redis
  watchdog cannot independently requeue managed attempts. Node counts resolve
  durable Worker IDs instead of GPU models. Builder callbacks remain separate.
- `Worker/{execution_state,scheduler_protocol,managed_worker}.py`, `main.py`,
  `hardware.py`, `executor.py`, `runtime_config.py`, `config.py`: shared host lock,
  crash-safe SQLite journal and claim UUID, conservative monotonic leases, dispatch
  through existing batch executor, persisted fenced results/logs, explicit polling
  guard, heartbeat during pause/runtime/cleanup, GPU process inventory, labelled
  batch launch paths and protected state/credential configuration.
- `Worker/interactive/{docker_ops,manager,endpoint,broker,cleanup}.py`: exact digest
  pull/inspection, registry allowlist and tmpfs auth, preflight quotas/NVIDIA,
  image User/WORKDIR/volume checks, bounded isolated workload/Access/sidecar,
  authenticated HMAC Unix broker, real fixed Docker exec PTY, resize/backpressure,
  pidfd/cgroup process cleanup with exact-container fallback, local Serve gating,
  exact cleanup, independently supervised lease guard and crash reconciliation.
- UI interactive details/service and `terminalVerification.ts`: separate build/
  runtime status, Start/Stop, READY polling, stale response fencing, in-memory grant,
  WSS auth/ready validation, incremental bounded OPENED/output/EXIT parser, CLOSE
  verification session and success only after workload connection/cleanup. Save
  stays disabled. The interactive test script includes all new UI tests.
- New unit/component tests, PostgreSQL `check_runtime.py`, CI runtime-broker manual
  gate, `test/interactive_e2e/runtime/` fixture and actual deployment/GPU/browser
  checker, `.env` examples/Compose overlay, systemd Worker/lease guard units and
  `docs/interactive-runtime-operations.md`.

## Verification actually run on the editing machine

All tests run with production secrets disabled and fake Docker/management adapters
where required. Python dependencies use a disposable `/tmp` virtual environment.
UI tests/build use Node 22. PostgreSQL gates use a disposable local cluster/schema,
no production database or runtime services.

| Check | Result |
| --- | --- |
| Scheduler unit suite, including runtime/controller/Worker API | 287 passed |
| Worker unit suite, including coordinator/polling/Docker adapter/broker | 286 passed |
| Access unit/component suite | 41 passed |
| Existing builder unit regressions | 143 passed |
| Gateway unit suite | 31 passed |
| Management unit suite | 32 passed |
| UI interactive/runtime/transport | 20 passed |
| Existing UI download regressions | 5 passed |
| User UI TypeScript and production build | Passed |
| PostgreSQL migration 001 upgrade/concurrent build/immutability gate | Passed |
| PostgreSQL migration 002/replay/estimation uniqueness/cross-kind race/cleanup/multiple reconcilers | Passed |
| Python syntax/unused imports and git whitespace | Passed |

## Not run here; acceptance still pending

- Real Docker-exec/PTTY/host-process cleanup gate on separate Ubuntu host/CI,
  including hostile sessions, repeats, exact workload fallback and sentinel.
- Actual Scheduler/Worker/Headscale/Tailscale/Gateway/browser deployment check.
- Real existing-builder registry digest/private pull, assigned physical NVIDIA
  GPU, working XFS/project quotas and resource/storage enforcement.
- Real estimation/interactive/retry/training priority and exclusion queues.
- SIGKILL/hung launch/partition/host restart/Docker restart scenarios with exact
  cleanup and unrelated container/image/volume/Headscale-node preservation.
- Public HTTPS/WSS browser success without Tailscale and actual multi-host/NAT
  routing with separate Gateway/control and Worker networks.

Passing mocked, CPU-fixture or single-host protocol tests is not production GPU,
registry, writable-storage, crash or multi-host acceptance. Leave admission off
until those actual host gates pass. The existing fake-PTY E2E is retained as a
protocol gate and does not count as execution inside a workload.

## Deployment sequence

Follow `docs/interactive-runtime-operations.md` for exact per-host commands.
Drain/reconcile legacy batch work, back up PostgreSQL, migrate with admission off,
provision distinct Worker/controller secrets, configure reachable HTTPS/CA/WSS and
management isolation, publish pinned Access/quota fixture images, validate NVIDIA
and XFS/project quotas on the actual Worker, install host systemd units, complete
startup reconciliation, run smoke/host gates, then enable claims and interactive
Start admission. Rollback first disables starts and drains/stops all new-format
assignments before deploying older processes. Never clear holds by TTL or missing
Redis mappings; do not use broad Docker pruning or Headscale resets.
