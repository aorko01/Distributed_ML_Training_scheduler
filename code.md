# Interactive Workflow — Files and Functions in Reading Order

End-to-end: Browser UI / VS Code + dml-ssh → Scheduler → Worker → Management → Access → Gateway → back.
Follows `interactive.md` Phase A→F. Generation is the fencing token. SSH reuses `workspace` service on tailnet `9000`.

## 0. Map — architecture + data model

1. `interactive.md`
   - Components, Phase A-F, `purpose=browser|ssh`, `OPEN` vs `SSH_OPEN`
2. `Scheduler/app/models/interactive_runtime_model.py`
   - `generation, ssh_capable, ssh_ready, ssh_status, state=QUEUED/READY`
3. `Scheduler/app/models/interactive_workspace_model.py`
   - `workspaces, revisions, IMAGE_READY`
4. `Scheduler/app/models/cli_token_model.py`
   - `interactive:ssh` scoped tokens, hashed refresh

## 1. UI — Create / poll / connect

5. `UI/User/src/pages/InteractiveWorkspaces.tsx`
   - list runtimes
6. `UI/User/src/pages/InteractiveCreate.tsx`
   - Start form → creation request
7. `UI/User/src/services/interactive.ts`
   - `creationRequest(input, key)`
   - `interactive.start(), latest(), mine(), stop(), connection(), workspaceConnection(), sshInfo(), sshConnection()`
   - `interactiveCapacity.options(), preview()`
8. `UI/User/src/pages/InteractiveDetails.tsx`
   - poll `latest()` + `ssh-info`, show VS Code command or blocker (old image / starting / sshd-failed)
9. `UI/User/src/features/workspace/WorkspaceIDE.tsx`
   - IDE shell
10. `UI/User/src/features/workspace/hooks/useWorkspaceConnection.ts`
    - `POST workspace-connection → WSS → HELLO workspace-stream-v1 → WORKSPACE_READY`
11. `UI/User/src/services/workspaceProtocol.ts`
    - `record(), metadataBytes(), decodeObject(), packChunk(), validRequestId(), describeCloseCode()`
12. `UI/User/src/services/terminalVerification.ts`
    - `record(), verifyConnection(grant, signal)`
13. `UI/User/src/features/workspace/workspaceReducer.ts`, `adapters.ts`, `monacoAdapter.ts`, `xtermAdapter.ts`
    - file/PTY records

## 2. Scheduler control-plane — pin spec + grants

14. `Scheduler/app/api/interactive_runtime_route.py`
    - `bounded()` — idempotency + rate limit
    - `start()` → create runtime `QUEUED, generation=N+1`
    - `latest(), mine(), stop()`
    - `connection(), workspace_connection()` — `purpose=browser`
    - `ssh_info(), ssh_connection()` — `purpose=ssh`
    - `cli_login(), cli_refresh(), cli_logout()`
15. `Scheduler/app/api/deps.py`
    - `get_current_active_user()` — browser JWT
    - `get_ssh_user()` — `interactive:ssh` scoped token only
16. `Scheduler/app/services/interactive_runtime_service.py`
    - `start(db, owner, workspace_id, key, body)` — ownership, IMAGE_READY, no live runtime, pin immutable spec
    - `requirements_from_spec(), assigned_machine_summary()`
    - `owned_runtime(), public(), _ssh_fields()`
    - `stop()` — withdraw, revoke
    - `connection(..., workspace=False/True)` — owner+generation+READY check, call Management, re-check + revoke on race
    - `ssh_grant_ready(), _ssh_blocker(), ready()`
    - `ssh_info()` — `ssh_user=dml, /workspace, host-key+fingerprint, no secrets`
    - `ssh_connection()` — burst 5/10s, single-use ticket
17. `Scheduler/app/services/interactive_management_client.py`
    - `ManagementClient.issue_grant(), revoke_grant()` → `POST /internal/v1/access-grants`
18. `Scheduler/app/services/interactive_controller.py`
    - `bootstrap(db, worker_id, body, management)` — claim assignment
    - `reconcile_one(db, management)` — health gate → READY
    - `tick(), run()`
19. `Scheduler/app/services/cli_auth_service.py`
    - `login(), issue_pair(), refresh(), logout(), revoke_all(), _hash()`
20. `Scheduler/app/services/interactive_capacity_service.py`
    - `options_payload(), preview_payload(), _fresh_workers(), _snapshot_for()`
21. `Scheduler/app/services/interactive_workspace_service.py`
    - `owned(), revision(), create(), claim(), fence(), mark_ready(), failure(), heartbeat(), logs()`

## 3. Worker — build 3 containers in order

Claim via `Worker/scheduler_protocol.py`, `Worker/managed_worker.py`, `Worker/server.py`, `Worker/api.py`.

22. `Worker/interactive/manager.py`
    - `Manager.execute(record)` — ordered: `verify digest/label/UID 10001/workdir → workload (GPU UUID) → tmpfs sshd keys → sshd smoke → broker smoke → Access+sidecar → enroll → Serve 9000 → probe → event READY + ssh_ready`
    - `progress(), authority(), stop(), capture_pending(), run()`
    - `max_duration_seconds(), time_up_detail(), notify_time_up(), smoke_workload()`
23. `Worker/interactive/docker_ops.py`
    - `DockerOps`, `labels(record, worker_id, component)` — `dml-<assignment>`
    - `ssh_spec_enabled(), ssh_allowed_locally(), ssh_image_capable(), workload_network_mode(), workload_developer_mode()`
24. `Worker/interactive/ssh.py`
    - `ssh_enabled(), sshd_config_text()`
    - `setup_workload_sshd(container)` — `127.0.0.1:2222`, tmpfs
    - `install_authorized_key(container, public_key)`
    - `ssh_smoke(), stop_workload_sshd(), SshRelay`
    - `parse_ed25519_public_key(), fingerprint_sha256()`
25. `Worker/interactive/broker.py`
    - `Broker.__init__(), start(), stop(), healthy(), handle(), relay_ssh()`
    - `DockerSession` — Docker-exec `/bin/sh`, no client-supplied container/command/path
    - `busy` (browser single slot) vs `ssh_active/ssh_max` (`INTERACTIVE_SSH_CAPACITY=8`)
    - generation compare, never select container from client
    - `workload_env()`
26. `Worker/interactive/workspace_broker.py`
    - `WorkspaceSession` — file service bridge
27. `Worker/interactive/file_service.py`
    - `FileService` — `parts(), rootfd(), parent(), version(), check_version(), out(), fail()`
28. `Worker/interactive/endpoint.py`
    - `Endpoint` — tailscale sidecar, join with one-time key (wiped), Serve `127.0.0.1:9000→9000`
29. `Worker/interactive/cleanup.py`
    - `sweep(state_dir, worker_id)` — exact-ID cleanup, wipe tmpfs/sockets
30. `Worker/interactive/snapshot.py`
    - `capture(), complete(), journal_snapshot(), load_snapshot()`
31. `Worker/interactive/failure_diagnostics.py`
    - `collect_failure_diagnostics()`

## 4. Management (Headscale_Management) — enrollment + tickets

32. `Headscale_Management/headscale_management/main.py`
    - `create_app()` — routes, roles
33. `Headscale_Management/headscale_management/schemas.py`
    - `Enroll, Register+Generation, Probe, Issue, Claim`
34. `Headscale_Management/headscale_management/enrollment_service.py`
    - `EnrollmentService.enroll(), register(), probe()` — endpoint identity
35. `Headscale_Management/headscale_management/endpoint_service.py`
    - `EndpointService` — resource versioning, leases
36. `Headscale_Management/headscale_management/grant_service.py`
    - `GrantService.issue(request, actor)` — Ed25519, `aud=gateway`, `purpose`, 60s admission, 4h ssh / 30m browser, 15s lease
    - `claim(), destination(db, session)` — never trust client input, fresh verified membership
    - `decode(), renew(), release(), revoke()`
37. `Headscale_Management/headscale_management/auth.py`, `config.py`, `models.py`, `reconciliation.py`
    - `authenticate(), require()`, `Settings`, `Enrollment/Endpoint/Grant/Session`, `Reconciler.verify_policy()`

## 5. Access container — byte relay, no Docker socket

38. `Access_Container/interactive_access/config.py`
    - `Config`, `read_secret()` — Unix socket path, token, SSH capacity
39. `Access_Container/interactive_access/main.py`
    - `Service`, `main()` — listen `127.0.0.1:9000`, tailnet only
40. `Access_Container/interactive_access/protocol.py`
    - `Type`, `encode(), parse_json(), ssh_open(), dimensions(), decode_header(), Parser, read_record(), write_record()`
    - first record: `OPEN` (browser) vs `SSH_OPEN{version:1, public_key, generation}` (ssh)
41. `Access_Container/interactive_access/workspace_protocol.py`
    - `metadata(), metadata_bytes(), request_id(), path(), pack_chunk()/unpack_chunk(), valid_chunk()`
42. `Access_Container/interactive_access/broker_client.py`
    - `connect(), ready(), _connect(), close_writer()` — challenge/HMAC over `/run/dml-interactive/<id>/`
43. `Access_Container/interactive_access/session.py`
    - `run_session()` — dispatch
    - `run_workspace_session()` — `HELLO → WORKSPACE_READY` → file/PTY
    - `run_ssh_session()` — generation check → install key → `SSH_READY` → raw relay
    - `Capacity`, `_ssh_acquire()/_ssh_release()`

## 6. Gateway — public WSS → private tailnet

44. `Gateway/interactive_gateway/main.py`
    - `create_app(settings, management, dialer)` — `wss://…/v1/connect/<resource>/<service>`
45. `Gateway/interactive_gateway/config.py`
    - `Settings`, `secret()`
46. `Gateway/interactive_gateway/schemas.py`
    - `Authenticate`, `timestamp(), destination()`
47. `Gateway/interactive_gateway/auth.py`
    - `verify(ticket, settings, resource_id, service)` — 5s auth, one-time claim
48. `Gateway/interactive_gateway/management_client.py`
    - `ManagementClient.claim(), renew(), release()` — authorization lease
49. `Gateway/interactive_gateway/tailnet_dialer.py`
    - `TailnetDialer.dial()` — tailnet TCP 9000 via private SOCKS5
50. `Gateway/interactive_gateway/relay.py`
    - `relay(websocket, reader, writer, ...)` — blind byte relay, byte counts only
51. `Gateway/interactive_gateway/sessions.py`, `tailscale_api.py`, `bootstrap.py`
    - `Capacity`, `TailscaleAPI`, `bootstrap(), main()`

## 7. dml-ssh CLI — laptop side, per-connection grant

52. `dml-ssh/src/dml_ssh/cli.py`
    - `build(), main()`
    - `cmd_login(args)` — `username/password → interactive:ssh` token, never browser JWT
    - `cmd_configure(args)` — fetch ssh-info, `ensure_key`, pin host-key, write `dml-config` stanza `Host dml-<runtime>-g<gen> + ProxyCommand`
    - `cmd_proxy(args)` — per-leg `ssh_grant → WSS auth → SSH_OPEN`
    - `cmd_doctor(), cmd_logout()`
53. `dml-ssh/src/dml_ssh/client.py`
    - `login(), _refresh(), ensure_access(), _authed(), _req()`
    - `ssh_info(base, token, ident)`
    - `ssh_grant(base, token, runtime_id)` — `POST /ssh-connection`, no-store `wss_url`
    - `proxy_stream(grant, public_key, generation, stdin, stdout)`
54. `dml-ssh/src/dml_ssh/sshconfig.py`
    - `ensure_key(), snippet(), write_snippet(), write_known_host(), prune_old_keys(), is_managed_alias()`
55. `dml-ssh/src/dml_ssh/store.py`
    - `save(), load(), load_scheduler(), update_access(), clear()` — `~/.ssh/dml-<id>`, `dml-config`, `known_hosts`

VS Code Remote-SSH flow: `Connect to Host → dml-…-g… → Open Folder /workspace`. Standard OpenSSH, `ProxyCommand dml-ssh proxy …`.

## 8. Verify end-to-end

56. `test/interactive_e2e/test_lifecycle.py` — Start → READY → browser connect → Stop
57. `test/interactive_e2e/test_terminal.py` + `test_gateway.py` — PTY + WSS ticket auth
58. `test/interactive_e2e/test_enrollment.py` + `test_faults.py` + `test_isolation.py` + `test_restart_unit.py`
59. `test/interactive_e2e/fixtures/controller.py`, `terminal_broker.py`, `agent.py`, `echo_endpoint.py`, `conftest.py`, `run.py`
60. Unit mirrors:
    - `Scheduler/test/unit/test_interactive_*.py` (`test_interactive_ssh.py`, `test_interactive_workspaces.py`, `test_interactive_capacity.py`)
    - `Worker/test/unit/test_ssh.py`, `test_runtime_broker.py`, `test_workspace_broker.py`
    - `Access_Container/test/unit/test_ssh.py`, `test_protocol.py`
    - `Gateway/test/unit/test_gateway.py`, `test_ssh_ticket.py`, `test_relay.py`
