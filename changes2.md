# Interactive workspace image and terminal-access phase

This document records the implementation added after the earlier interactive
access infrastructure work. It covers image creation and the generic terminal
access endpoint. It does **not** add Worker placement, a production Docker-exec
broker, a live Connect action, or snapshot/save support.

## Scope and runtime boundary

- The development machine only contains source code and credential-free tests.
  The Docker Image Builder, registry interactions, Docker/Tailscale E2E suite,
  and production services are intended to run on a separate Docker-enabled
  Ubuntu host.
- A user workload image remains separate from Access, Tailscale, registry
  credentials, and Docker control. Access has no Docker socket, Docker client,
  SSH daemon, Tailscale binary, registry credentials, or submitted project
  files.
- The future runtime path is browser terminal → Gateway → Tailscale endpoint →
  Access on TCP 9000 → runtime-bound Unix broker socket → Worker Docker exec →
  workload PTY. The current fake broker is test-only and creates a local PTY;
  it never represents production Docker exec or GPU access.

## Access_Container

- Added `Access_Container/`, a pinned Python 3.11 slim image running as numeric
  user `10001:10001`. It has a read-only-root compatible design, loopback TCP
  terminal listener on port 9000, and a separate loopback health endpoint on
  port 9002.
- Added `docs/terminal-stream-v1.md`. The protocol is a versioned six-byte
  length-prefixed record stream with `OPEN`, `STDIN`, `RESIZE`, `CLOSE`,
  `OPENED`, `STDOUT`, `EXIT`, and public `ERROR` records. It supports fragmented
  and coalesced data and keeps records at or below Gateway's 64 KiB frame limit.
- Enforced one OPEN first, strict JSON schemas/dimension limits, no arbitrary
  command/container/user/mount fields, bounded payloads/buffers, capacity
  limits, deadlines, and shutdown cleanup. The Access listener is withdrawn if
  its broker readiness check fails.
- Added a narrow Unix-socket broker contract. Each connection receives a fresh
  challenge and responds with HMAC-SHA256 using a per-runtime protected token.
  Tokens are read with no-follow regular-file checks and restrictive permissions.
  The contract exposes only the configured default shell, input, resize, output,
  exit, and close operations.
- Added live/ready health checks. Readiness validates configuration, socket,
  token permissions, broker authentication, and a bounded broker probe without
  returning backend details.
- Added parser, deadline-clock, session cleanup, capacity, token-permission,
  fake-broker PTY, resize, control-character, large-output, replay, image
  isolation, restart, and health tests. The fake broker verifies the PTY's
  workload cwd/environment rather than using Access's own environment.

## Scheduler interactive image model and APIs

- Added separate `InteractiveWorkspace` and `InteractiveImageRevision` models.
  They are independent of the batch `Job` state machine and therefore never
  enter VRAM estimation, runnable, or in-progress states.
- Added immutable workspace provenance and revision provenance, monotonic
  revision numbers, queue/build/ready/failure/cancelled states, builder lease
  fields, attempt fencing, tags, canonical digest references, and safe build
  stages.
- Added additive PostgreSQL migration
  `Scheduler/migrations/001_interactive_workspaces.sql`. It creates constraints,
  indexes, source-owner consistency, immutable ready revisions, and immutable
  provenance. Startup uses an advisory lock before applying it. SQLite remains
  available for model-based development tests.
- Added owner-authenticated routes under `/interactive/workspaces`:

  - `POST /from-upload` accepts only a name, allowlisted base image identifier,
    and ZIP archive.
  - `POST /from-job` derives an image only from the current user's eligible
    server-owned batch job image.
  - list, detail, build-log, source-job, base-image, and cancellation routes
    return only the active user's safe information.

- Creation uses an owner-scoped idempotency key and request hash. Another user's
  IDs return 404, preventing existence disclosure. Object-store keys and source
  image tags are not returned to the browser.
- Added authenticated internal builder routes for claim, heartbeat, log stage,
  ready, failure, and release. They require a high-entropy credential from a
  protected file and reject unknown request fields. Every callback is fenced by
  builder ID and attempt ID.
- ZIP validation rejects bad archives, path traversal, symlinks/special files,
  duplicate paths, submitted Dockerfiles, decompression/file-count overages, and
  missing `requirements.txt`.

## Docker Image Builder

- Added a separate interactive queue alongside batch work. Queue preference
  alternates so neither queue can starve the other.
- Added interactive upload builds: download and safely extract the archive,
  resolve the allowlisted base image to a digest, generate a workload-only
  Dockerfile, build/push an attempt-specific tag, resolve its canonical digest,
  then publish only through the matching Scheduler attempt callback.
- Added existing-job builds: resolve the server-provided job image to a digest,
  make a minimal provenance-labelled derived image, push a distinct revision
  tag, and report its digest.
- Build/pull/push operations are cancellable and classify actionable archive or
  dependency failures separately from daemon, network, registry, and
  authentication failures. Raw Docker/pip output is never exposed through the
  new user-visible build-log route; only safe stage messages are stored.
- Added an exact attempt artifact ledger for tag/digest retention review. It is
  intentionally not a wildcard cleanup mechanism. Operators can identify an
  orphan left after a push but before its callback.
- Added Scheduler and Builder Compose overlays that mount the shared builder
  credential from runtime-host protected files. Builder Docker CLI credentials
  are held in private tmpfs storage and are not copied into workload images.

## UI

- Added a Batch job / Interactive workspace choice to the submission page.
  Batch fields and behavior remain intact.
- Added interactive upload and existing-owned-job forms. Interactive creation
  intentionally has no run command, resume command, priority, VRAM, or placement
  controls.
- Added workspace list and detail pages showing source, revision state, safe
  build stages, tag/digest abbreviation, and safe failure state.
- Connect and Save as new revision are visibly disabled with an explanation that
  runtime placement is not yet available. API failures do not substitute mock or
  cached data.
- Added Vitest/Testing Library coverage for source switching, request shape,
  idempotency retry, double-submit prevention, owner-job empty/error states,
  all build states, disabled runtime controls, error retry, and unchanged batch
  submission.

## Docker/Tailscale test harness and CI

- Extended `test/interactive_e2e/` with Access plus a fake runtime PTY broker.
  It uses the existing pinned Headscale/Tailscale/Gateway topology and verifies
  fragmented terminal traffic through WSS, SOCKS, tailnet TCP, and Access.
- Added checks for owner isolation, wrong service rejection, ticket replay,
  terminal cwd/environment, resize, child reaping, lateral endpoint denial,
  grant/resource revocation, endpoint offline, Access restart, management lease
  loss, and Gateway shutdown.
- Added image inspection checks for Access's non-root user, read-only filesystem,
  no Docker socket mount, absence of Docker/SSH/Tailscale tooling, and absence
  of user workload files.
- Extended GitHub Actions with Access unit/component tests, UI tests/build/lint,
  a disposable PostgreSQL migration/concurrent-claim/immutability gate, and the
  terminal network suite. The large Docker Hub test for both image sources is
  opt-in through workflow dispatch because it needs real credentials and pulls
  large CUDA images.

## Documentation and operations

- Added `docs/interactive-images-operations.md` with migration, secret, runtime
  host, verification, image retention, orphan handling, decommissioning, and
  future Worker handoff instructions.
- The document explicitly distinguishes local development checks from runtime
  host verification. It gives the commands and prerequisites for PostgreSQL,
  Docker/Tailscale, and optional Docker Hub tests without assuming this source
  machine runs those services.
- The Worker handoff specifies digest pulls, runtime labels/generation fencing,
  broker socket/token isolation, no-network-by-default workloads, default
  seccomp/capability restrictions, `--init`, resource limits, and explicit
  snapshot constraints. Docker commit must never be assumed to include mounted
  workspace volumes.

## Verification completed on the development machine

- Scheduler unit tests: 277 passed.
- Docker Image Builder unit tests: 143 passed.
- Access unit and component tests: 37 passed.
- User UI interactive tests: 12 passed; existing UI tests, production build, and
  lint also passed.
- Python source syntax, CI/Compose YAML validation, and Docker Hub E2E skip
  behavior were checked.

## Verification still required on the runtime host or CI

- Run the disposable PostgreSQL migration gate against a real PostgreSQL server.
- Run the Docker/Tailscale E2E suite on Docker-enabled Ubuntu.
- Run the opt-in Docker Hub gate with actual registry credentials to build, push,
  pull by digest, and inspect both interactive image sources.
- These gates do not claim that production Worker Docker exec, GPU allocation,
  cross-host NAT, placement, or save/snapshot behavior exists. Those remain the
  next Worker-runtime phase.
