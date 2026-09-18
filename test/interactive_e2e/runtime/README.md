# Production runtime acceptance gates

Run these on a separately provisioned Ubuntu Docker/Worker host, never the editing
machine. `check_broker.py` uses the real host Docker-exec broker against a digest
fixture, validates workload identity/PTY/resize and normal/hostile/repeated session
cleanup while preserving a sentinel. CPU fixture results do not prove GPU/quota/NAT.

Build `Dockerfile`, push to a **disposable** registry, and provide its exact digest
as `RUNTIME_FIXTURE_DIGEST`. Then run as root on that Docker host:

```sh
sudo --preserve-env=RUNTIME_FIXTURE_DIGEST /opt/dml/venv/bin/python test/interactive_e2e/runtime/check_broker.py
```

`check_deployment.py` exercises actual deployed Scheduler Start/status/grants/Stop,
real Worker/container identity, assigned NVIDIA UUID, WSS terminal OPENED/CLOSE,
Worker journal heartbeat/claim counters and optional Chromium UI success. It
requires an owned disposable IMAGE_READY workspace and protected login token;
no test-only management controller or PTY fixture is accepted. Supply `--ui-url`
for the browser gate. Install Chromium on that host with `python -m playwright
install chromium` and invoke the commands in `docs/interactive-runtime-operations.md`.

The existing `../run.py` suite remains a separate Access/Gateway/Headscale protocol
gate with a fake local PTY. It does not execute these runtime-host scripts and
must never count as workload/GPU acceptance. CI's manual `runtime_docker` option
runs the portable real Docker broker fixture; full deployment/GPU/multi-host/crash
gates require the actual runtime environment and remain explicitly pending until
run. Keep raw terminal bytes, inspect/env data and secrets out of artifacts.
