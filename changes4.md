# Workspace editor implementation handoff

This change adds the editor transport and admission foundations from `plan3.md`.
It deliberately does not claim that snapshot publication or training handoff is
complete: their feature flags remain off, and the UI does not report a saved
revision or queued batch job.

## Implemented

- A distinct `workspace-stream-v1` protocol, documented in
  `docs/workspace-stream-v1.md`, with a strict HELLO/READY handshake, bounded
  file chunks, request IDs, cancellation, and a separate PTY lifecycle.
- Runtime-pinned access service/application protocol fields. Existing runtimes
  remain terminal-only; new editor-capable generations register `workspace` and
  retain the legacy terminal OPEN verification path.
- Fresh workspace connection grants, an authenticated editor route, local
  Monaco/xterm bundles, in-memory workspace socket state, real terminal bytes,
  and explicit live-file-save messaging.
- A Worker-bound file helper that runs with Docker exec in the exact workload
  User. It uses a fixed interpreter/program, descriptor-relative no-follow
  operations, UTF-8/size/type checks, version preconditions, bounded atomic
  writes, and does not expose Worker paths or Docker arguments to the browser.
- Additive `003_workspace_editor_snapshots.sql` and models for immutable save,
  artifact, snapshot provenance, and training-submission records. Owner APIs
  create/retrieve durable requested workflow records only when their feature
  flags are enabled.

## Admission and remaining stages

`WORKSPACE_EDITOR_ENABLED`, `WORKSPACE_SAVE_ENABLED`, and
`WORKSPACE_TRAINING_SUBMISSION_ENABLED` default to `0`. Do not enable save or
training submission until Worker journalled Docker commit/export, private
artifact capabilities/receipts, Builder SNAPSHOT import/publication, release
gating, and snapshot-source batch derivation have been implemented and accepted
on the required PostgreSQL, Docker/browser, GPU, and multi-host gates.

The checked local regressions are Scheduler runtime/workspace/Worker execution
(39 tests), Access unit/component terminal tests (39), Worker broker tests (3),
and UI TypeScript (`npm exec tsc -b`). Vite production bundling needs Node
20.19+; this workspace currently supplies Node 18.19.
