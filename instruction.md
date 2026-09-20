# Implementation instructions: production-quality browser workspace editor and terminal

## 1. Mission

Implement and finish the interactive workspace experience in this repository. The result must be a reliable, good-looking, dark browser IDE inspired by VS Code (not a pixel-for-pixel clone) where an authenticated user can:

- browse the project tree recursively;
- open several text files in tabs;
- edit and save files inside the live workload container;
- create, rename, and delete files and directories;
- use a real interactive shell in the same container, user, working directory, and runtime as the editor;
- resize, restart, and use the terminal naturally;
- recover cleanly from network interruptions and runtime restarts;
- understand which changes are live-only and which have been saved durably;
- stop the runtime without the UI pretending unsaved work is safe.

The implementation should feel like a small, coherent IDE, not a collection of raw controls. Do not attempt to reproduce every VS Code feature. A strong first release needs an explorer, tabs, Monaco editor, search within the current file, keyboard shortcuts, status bar, terminal, file operations, dirty-state protection, actionable errors, and robust connection handling.

This document is an implementation handoff. Read the referenced source before changing it, preserve the existing security boundaries, implement in small reviewable stages, and keep all existing terminal-only runtimes working.

## 2. Current repository state

The foundations already exist. Do not replace them with an unrelated REST file API, SSH-in-the-browser package, code-server, or an iframe.

### Frontend

- Route: `UI/User/src/App.tsx`, `/interactive/:id/editor`.
- Current page: `UI/User/src/pages/InteractiveEditor.tsx`.
- Browser protocol client: `UI/User/src/services/workspaceProtocol.ts`.
- Runtime/Scheduler API client: `UI/User/src/services/interactive.ts`.
- Existing design tokens and editor CSS: `UI/User/src/index.css`.
- Monaco `0.52.0`, Xterm `5.5.0`, and `@xterm/addon-fit` are already local dependencies. Do not load editor or terminal code from a CDN.
- Existing protocol test: `UI/User/test/workspaceProtocol.test.ts`.

### Scheduler and grants

- Owner routes: `Scheduler/app/api/interactive_runtime_route.py`.
- Runtime admission and connection grants: `Scheduler/app/services/interactive_runtime_service.py`.
- A workspace connection is requested with `POST /interactive/runtimes/{runtime_id}/workspace-connection`.
- An editor-capable runtime is immutable and pinned to:
  - `access_service = "workspace"`;
  - `application_protocol = "workspace-stream-v1"`;
  - `editor_capable = true`.
- Existing terminal-only runtimes must remain terminal-only. Never mutate one into a workspace runtime in place; start a new generation.
- `WORKSPACE_EDITOR_ENABLED` defaults to `0`. Rollout instructions must explicitly enable it only after tests and deployment gates pass.
- Save/revision and training-submission database/API foundations exist, but their Worker/Builder completion is not done. `WORKSPACE_SAVE_ENABLED` and `WORKSPACE_TRAINING_SUBMISSION_ENABLED` must stay off until the durable pipeline is actually implemented and tested.

### Transport and backend

- Protocol specification: `docs/workspace-stream-v1.md`.
- Shared framing/types: `Access_Container/interactive_access/protocol.py`.
- Workspace validation/relay: `Access_Container/interactive_access/session.py` and `workspace_protocol.py`.
- Worker broker endpoint: `Worker/interactive/workspace_broker.py`.
- Safe container file helper: `Worker/interactive/file_service.py`.
- Gateway is an authenticated byte relay; see `Gateway/interactive_gateway/relay.py`.
- Browser flow:

```text
React UI
  -> Scheduler owner-authenticated workspace grant
  -> public WSS Gateway; authenticate one-time ticket
  -> tcp-stream-v1 byte relay over the private network
  -> Access Container validates workspace-stream-v1 records
  -> authenticated Worker Unix broker
  -> WorkspaceSession
       -> fixed file helper via Docker exec in the workload
       -> real Docker exec PTY in that same workload
```

The browser must never receive or choose a container ID, Docker command, host path, Worker address, broker token, or tailnet address.

## 3. Fix the present disconnect before doing UI work

There is an immediate protocol-client defect in `UI/User/src/services/workspaceProtocol.ts`:

- the UI calls `openPty()` after the initial root listing;
- the Worker correctly responds with `PTY_OPENED` (`WorkspaceType.PTY_OPENED`, numeric type `39`);
- `WorkspaceConnection.receive()` does not handle `PTY_OPENED`;
- its final fallback throws `Workspace protocol error`;
- the WebSocket is then closed, all pending requests reject, and the UI reports a disconnected workspace.

Because PTY startup and user interaction are asynchronous, it can look as if clicking a file caused the disconnect even though the unhandled terminal reply is the trigger.

First implementation task:

1. Add an explicit `PTY_OPENED` branch.
2. Strictly decode and validate its metadata (`protocol` must be `workspace-stream-v1`).
3. Track the PTY lifecycle (`closed`, `opening`, `open`, `closing`, `exited`) rather than treating it as an incidental callback.
4. Expose a PTY state callback to the UI.
5. Do not send stdin or resize messages before `PTY_OPENED`.
6. Make repeated “New Terminal” clicks idempotent while opening/open, or explicitly close and await the old terminal before opening a new one.
7. Add a protocol regression test that completes Gateway ready, workspace READY, root `list`, `PTY_OPENED`, a streamed file `read`, PTY output, and PTY exit without closing the socket.

Also test frame splitting and coalescing. WebSocket messages do not necessarily correspond one-to-one with application records because the Gateway relays a TCP stream. The existing `partial` parser is meant to handle this and must keep doing so.

Do not hide this bug with delayed terminal startup or by ignoring every unknown record. Known records must be handled; unknown or invalid records must still fail closed.

## 4. Definition of the first production release

The release is complete only when all of the following work against a real editor-capable runtime:

1. Open the editor from a READY workspace.
2. See a recursive explorer rooted at the Worker-pinned workspace root.
3. Expand and collapse directories without disconnecting.
4. Open UTF-8 files up to the advertised limit in Monaco.
5. Edit, undo/redo, and save with `Ctrl/Cmd+S`.
6. Open multiple files and switch tabs without losing unsaved models.
7. Create, rename, and delete files/folders with clear confirmation for destructive actions.
8. Detect an external terminal modification and prevent silent overwrite through version preconditions.
9. Run commands in a real shell; receive ANSI output; use interactive programs; resize with the panel.
10. Restart a shell after it exits without losing file access.
11. Show connection, runtime, dirty, save, terminal, and read-only states accurately.
12. Reconnect using a fresh grant after an unexpected disconnect, without automatically discarding dirty in-browser models.
13. Warn before closing/navigating with dirty files.
14. Work on common desktop widths and remain usable, with a simplified layout, on narrow screens.
15. Pass unit/component tests and a real browser-to-container end-to-end test.

Durable “Save for Later” and “Submit for Training” are a separate release gate described in section 15. Live file save means “written to the running container,” not “survives runtime destruction.” Never conflate those two states in labels or messages.

## 5. Product layout and visual direction

Build a full-height IDE shell within the authenticated application. Prefer a focused editor route with minimal surrounding dashboard chrome. The editor should use the available viewport rather than inheriting the normal card/page padding.

Recommended structure:

```text
+--------------------------------------------------------------------------------+
| <- Workspaces | workspace-name | runtime READY | live-only | Save All | ...    |
+----+----------------------+----------------------------------------------------+
|    | EXPLORER             | file-a.py  x | README.md  x*                       |
| A  | v project            +----------------------------------------------------+
| c  |   v src              |                                                    |
| t  |       train.py       |                 Monaco editor                      |
| i  |     README.md        |                                                    |
| v  |   > tests            |                                                    |
| i  |                      +----------------------------------------------------+
| t  |                      | TERMINAL  1 x | + | trash          maximize/collapse|
| y  |                      | $ python train.py                                  |
+----+----------------------+----------------------------------------------------+
| main | Ln 12, Col 8 | Spaces: 2 | UTF-8 | Python | Connected | 1 unsaved      |
+--------------------------------------------------------------------------------+
```

Use a VS Code-like dark palette but retain this application's identity:

- workbench background near `#181818`;
- editor/terminal near `#1e1e1e`;
- sidebars near `#181818` or `#202020`;
- active surfaces near `#252526`;
- borders around `#2b2b2b`;
- primary text around `#d4d4d4`;
- muted text around `#8b8b8b`;
- blue accent consistent with the existing `--accent-primary`;
- green/yellow/red only for meaningful states.

Requirements:

- Use CSS variables and component classes, not large inline-style blocks.
- Use `lucide-react` for icons already available in the package. Give icon-only controls `aria-label` and `title`.
- Keep the code font local/system-first. Do not add a runtime dependency on Google Fonts; the current CSS import should not be necessary for editor correctness.
- Controls need visible hover, active, focus-visible, and disabled states.
- Use 4–6 px radii sparingly. IDE panes should feel dense, not like dashboard cards.
- Minimum hit target should be about 28 px on desktop and larger on touch/narrow layouts.
- Explorer and terminal widths/heights must be resizable with keyboard-accessible separators or sensible button alternatives.
- Persist non-sensitive layout preferences (explorer width, terminal height/collapsed state, word wrap, minimap choice) in `localStorage`, namespaced by application and schema version. Never store grants or file content there.
- At widths below roughly 800 px, allow toggling Explorer and Terminal rather than squeezing all three panes into unusable sizes.

## 6. Refactor the current single component

`InteractiveEditor.tsx` currently combines API loading, WebSocket lifecycle, Monaco, Xterm, explorer, tabs, and all rendering. Split responsibilities before adding many features. A reasonable structure is:

```text
UI/User/src/features/workspace/
  WorkspaceIDE.tsx
  workspaceTypes.ts
  workspaceReducer.ts
  hooks/
    useWorkspaceConnection.ts
    useEditorModels.ts
    useBeforeUnloadDirtyGuard.ts
    useResizablePanels.ts
  components/
    IDETitleBar.tsx
    ActivityBar.tsx
    ExplorerPanel.tsx
    ExplorerTree.tsx
    EditorTabs.tsx
    EditorPane.tsx
    TerminalPanel.tsx
    StatusBar.tsx
    Dialog.tsx
    ToastRegion.tsx
```

This exact naming is optional, but the boundaries are not. Protocol and transport state must not be entangled with presentational tree rows.

Keep state explicit. At minimum model:

- runtime identity: workspace ID, runtime ID, generation, revision ID;
- connection: idle, requesting grant, connecting gateway, handshaking workspace, connected, reconnecting, disconnected, fatal;
- capabilities and server limits from `WORKSPACE_READY`;
- explorer nodes keyed by normalized relative path, including loading/error/expanded states;
- one Monaco model per open path;
- tab order, active path, preview/pinned state if preview tabs are implemented;
- server version, last saved text/version, dirty state, saving state, and conflict state per file;
- PTY lifecycle independent of workspace socket lifecycle;
- user-visible notices/errors with stable IDs rather than one global error string.

Use a reducer or a small external-free store. Do not add a state-management dependency solely for this page.

## 7. Connection lifecycle and reconnect behavior

### Initial connect

1. Fetch workspace detail and latest runtime.
2. Verify `state === READY`, `desired_state === RUNNING`, `editor_capable === true`, `access_service === workspace`, and the expected application protocol.
3. Request a fresh, single-use workspace connection grant.
4. Verify the returned runtime ID and generation match the page's runtime.
5. Open WSS and send the Gateway ticket only after `onopen`.
6. Require the Gateway text ready message with `tcp-stream-v1`.
7. Send binary workspace `HELLO`.
8. Require and retain `WORKSPACE_READY` capabilities/limits.
9. Only then enable file and PTY commands.

### Disconnection

Map close/error conditions into useful UI messages. Preserve the numeric Gateway close code for diagnostics, but translate common cases:

- normal/idle close: “Session closed”; offer reconnect;
- authorization/lease expiry (for example `4410`): request a fresh grant;
- policy/protocol rejection (for example `4403`): do not spin in an infinite retry; show a diagnostic and one manual retry;
- runtime no longer READY/generation changed: return to details or offer to connect to the new generation;
- network loss/`1006`: retry with bounded exponential backoff and jitter.

Automatic reconnect rules:

- maximum a small bounded number of immediate attempts (for example 5), then manual retry;
- request a fresh grant for each new WSS; never reuse a ticket;
- only one connect/reconnect operation at once;
- abort timers and fetches on unmount or route change;
- fence every callback by runtime ID, generation, and a local connection epoch so stale sockets cannot update the new page;
- retain dirty Monaco models in memory while reconnecting;
- reload the explorer after reconnect;
- for each open clean file, re-read or stat it and update if changed;
- for each dirty file, stat it. If the server version changed, mark a conflict and do not autosave;
- open a new PTY after reconnection only if the previous panel was active, and clearly state that terminal process state was lost.

The socket's `close()` must be idempotent. It must not recursively overwrite a more useful first error with a later generic close event. Pending requests must reject exactly once and timers/listeners must be removed.

Add per-request timeouts and cancellation. A hung read must not leave a promise in `pending` forever. Sending `CANCEL` is appropriate when the protocol request has started; local cleanup is still required if the transport is already gone.

## 8. Protocol client requirements

Keep `workspace-stream-v1` strict. Improve the TypeScript client rather than weakening server validation.

### Parser and validation

- Retain the six-byte header: version byte, type byte, big-endian payload length.
- Bound the retained partial buffer. An attacker or corrupt peer must not grow it indefinitely.
- Validate exact metadata shapes, types, limits, and required values per record instead of broadly casting `Record<string, unknown>`.
- Validate `READY`, `RESULT`, `END`, `PTY_OPENED`, `PTY_EXIT`, `STATE`, and `ERROR` separately.
- Verify streamed read byte count and SHA-256 from `FILE_END` before decoding/returning content. The current browser client assembles chunks but does not verify the advertised size/digest.
- Use one `TextDecoder` policy intentionally. Invalid UTF-8 must yield an `UNSUPPORTED_FILE`-style user error, not corrupt replacement characters.
- Reject duplicate terminal opens, invalid state transitions, unexpected chunk sequences, duplicate END, and chunks for unknown/cancelled requests.
- Do not log grants, tickets, file content, terminal input/output, or raw frames.

### Concurrency

The protocol document says multiple reads and one mutation may be supported, but the current Worker implementation processes requests serially and accepts only one pending streamed write. Start conservatively:

- cap read/stat/list requests in the browser;
- serialize mutations and streamed writes;
- use unique request IDs;
- never interleave chunks belonging to different writes unless both client and server are deliberately upgraded and tested;
- isolate PTY state from file-request state.

If backend concurrency is upgraded, guard all writes to a shared `asyncio.StreamWriter` with one writer lock or a single outbound queue. FILE responses and PTY output are produced by different tasks and must never create invalid record ordering or write-after-close races.

### Error model

Introduce a typed client error with fields such as `code`, `message`, `retryable`, `operation`, and optional `path`. Translate server codes in one place:

- `NOT_FOUND`;
- `CONFLICT`;
- `READ_ONLY`;
- `UNSUPPORTED_FILE` / `UNSAFE_FILE`;
- `TOO_LARGE`;
- `INVALID_PATH`;
- `BUSY`;
- `CANCELLED`;
- `UNAVAILABLE`;
- `PROTOCOL_ERROR`.

Do not disconnect the whole workspace for an ordinary operation-level `FILE_RESULT.error`. Do disconnect for malformed framing, impossible state, or invalid global records.

## 9. Explorer requirements

The current page only lists the root and disables directories. Implement a lazy recursive tree.

- Root label should use the workspace name, while protocol paths remain relative.
- Expanding a directory calls `list(path)` and renders children in stable name order (directories first, then files, case-insensitive with deterministic tie-breaking).
- Honor `next_cursor`; fetch all pages or provide incremental “load more.” Never silently omit the 201st entry.
- Cache loaded directory children, but provide Refresh on the root and per directory.
- File identity is its full relative path, not basename.
- Never generate paths with a leading slash, `.` segment, `..`, empty segment, backslash, or NUL.
- Provide loading skeleton/spinner, empty folder, and local error states without replacing the whole IDE.
- Visually distinguish supported files, folders, symlinks, and unsupported nodes. Symlinks/devices/etc. must remain non-openable because the backend intentionally refuses unsafe types.
- Use language/file icons by extension without requiring a giant icon package.

Add context-menu and toolbar operations:

- new file;
- new folder;
- rename;
- delete;
- refresh;
- copy relative path (safe browser clipboard API with fallback message).

Use an inline input or accessible dialog. Validate names before sending. After a mutation, refresh the affected parent, maintain selection when sensible, and update open tabs/models after rename. Before delete, show the exact relative path and whether it is a non-empty directory. The current helper only removes empty directories; report that limitation clearly unless recursive delete is separately designed with strict bounds and confirmation.

For rename/delete, obtain or retain the correct expected version. Do not pass `null` blindly where the server semantics require a version. Directory version semantics may need a small protocol/backend extension; specify and test it rather than silently removing conflict protection.

## 10. Monaco editor requirements

Use Monaco models rather than repeatedly calling `editor.setValue()` on one anonymous model. Calling `setValue()` damages undo history and makes per-tab dirty tracking fragile.

For every open file:

1. Read it through the protocol.
2. Create a URI such as `dml-workspace://<runtime-id>/<encoded-relative-path>` without exposing a host path.
3. Create/reuse one Monaco model for that URI.
4. Choose language by extension/basename (`.py`, `.js`, `.ts`, `.tsx`, `.json`, `.yaml`, `.yml`, `.md`, `.sh`, Dockerfile, requirements files, plain text fallback).
5. Attach one content listener that updates that file's dirty state.
6. Switch active files with `editor.setModel(model)`.
7. Dispose the model/listener when the tab is truly closed or the page is destroyed.

Recommended Monaco options:

- `theme: "vs-dark"` initially, or a small custom dark theme consistent with the shell;
- automatic layout plus explicit `layout()` after panel resizing;
- bracket-pair colorization;
- render whitespace on selection;
- smooth scrolling;
- format-on-paste where supported;
- minimap off by default on small panels, user-toggleable;
- word wrap off by default, user-toggleable;
- font size around 13–14 with common monospace fallbacks;
- do not enable unsupported “format document” promises without a formatter.

Tabs:

- active tab styling and close control;
- dirty dot separate from close affordance;
- middle-click close where practical;
- close others / close saved via context menu is optional but useful;
- closing a dirty tab must ask Save, Don't Save, or Cancel;
- display basename prominently and parent path in tooltip/secondary text when duplicate basenames are open;
- tab overflow must scroll rather than compress labels to zero.

Keyboard commands:

- `Ctrl/Cmd+S`: save active;
- `Ctrl/Cmd+Shift+S`: save all (there is no local “Save As” in the first release);
- `Ctrl/Cmd+W`: close active tab with dirty guard;
- `Ctrl/Cmd+P`: optional quick-open once the recursive index exists;
- `Ctrl/Cmd+F`: Monaco's built-in current-file search;
- ``Ctrl+` ``: toggle/focus terminal;
- `Ctrl/Cmd+B`: toggle explorer.

Do not intercept browser shortcuts globally when focus is outside the IDE unless intentional and documented.

### Saving and conflicts

The authoritative save is `write(path, content, expected_version)`.

- The expected version must be the version returned by the file's last successful read/write.
- Mark saving while awaiting the result.
- Mark clean only if the model still contains the exact text that was sent. If the user typed during the request, update the server version but leave the model dirty.
- On `CONFLICT`, never overwrite automatically. Present three explicit paths:
  - reload server version (after warning that local edits will be discarded);
  - keep local content and open a comparison view with server content;
  - copy/download local content so work cannot be lost.
- Do not add force overwrite until it has explicit protocol semantics and an intentional UX.
- Saving one tab must use that tab's model, not whatever editor happens to be active. The current `save(tab)` reads `editor.current.getValue()`, which can save the wrong content during Save All.
- Save All should serialize writes initially, continue/report per-file failures, and never claim success if one failed.

Add `beforeunload` protection and an in-app route blocker for dirty models. Runtime Stop should also warn that live container changes may disappear unless a durable revision has completed.

## 11. Terminal requirements

Xterm is the presentation for a real PTY supplied by `WorkspaceSession`; do not emulate a shell in JavaScript.

- Instantiate Xterm only when its host is mounted and visible enough to measure.
- Use `FitAddon`, call `fit()` after panel visibility/size changes, and send resize only after PTY is open and dimensions actually changed.
- Debounce `ResizeObserver` events.
- Write PTY bytes as bytes/UTF-8 supported by Xterm; do not force `convertEol: true` if it changes real PTY semantics. Test prompts, carriage returns, progress bars, colors, `clear`, `vim`/`nano`, and Python REPL behavior.
- Buffer user keystrokes only for a very short opening window or disable input until `PTY_OPENED`; never send stdin to a closed PTY.
- Show terminal state in its header: starting, running, exited with code, disconnected.
- “New Terminal” in the initial protocol means restart the single supported PTY. Do not present multiple numbered terminals unless the protocol/backend is extended to carry PTY IDs.
- Add restart, clear display, maximize/restore, collapse, and close controls.
- Closing PTY must leave explorer/editor connected.
- PTY exit must leave explorer/editor connected.
- Workspace socket loss terminates PTY UI state, but dirty editor models remain in memory for reconnect.
- On unmount, send PTY close/workspace close best-effort, remove listeners, dispose Xterm/addons/observers, and then close the socket.

Clipboard behavior should use Xterm selection and browser permissions. Pasting multiline commands should show a lightweight warning if feasible, especially when bracketed paste is unavailable. Do not capture or persist terminal history on the frontend.

The shell must start in the Worker-pinned workspace root and as the runtime's configured workload user. Verify with `pwd`, `id`, file creation from both terminal and editor, installed Python environment, and GPU visibility on a GPU deployment. “Like a VM” means the user can operate freely inside the assigned container under its configured permissions; it does not authorize host access or weakening container isolation.

## 12. Backend changes likely needed

Keep the current defense-in-depth model:

- browser paths are untrusted relative paths;
- Worker pins container ID, user, and workspace root from trusted runtime state;
- file operations execute a fixed helper program/argument vector;
- no-follow, descriptor-relative file access prevents symlink escapes;
- text/file/page/record limits remain bounded;
- expected versions prevent silent cooperative overwrites;
- Gateway remains an opaque authenticated relay.

Review and address these implementation gaps:

1. `WorkspaceSession` currently documents read/mutation concurrency that it does not implement. Either implement bounded task concurrency safely or correct the advertised contract and let the client serialize.
2. Protect concurrent broker writes (PTY pump plus file responses) with a per-session send lock or single outbound queue. Preserve bounded backpressure and cancellation.
3. Make session shutdown idempotent. `close_pty()` must not emit duplicate exits or write after the transport has closed.
4. Emit meaningful `WORKSPACE_STATE` changes for read-only/draining/runtime-stop conditions if the UI is expected to consume them.
5. Decide directory version semantics for safe rename/delete and add tests.
6. Consider a bounded server-side file-watch capability only after the core release. Do not poll every file aggressively or stream arbitrary filesystem events without coalescing and limits. A simpler refresh/stat-on-focus strategy is acceptable for v1.
7. Ensure the workload always has the fixed helper interpreter. `FileService` invokes `python3`; editor admission must fail clearly if the image lacks it, or the helper must be supplied in a controlled way that does not depend on user project files.
8. Improve observability using IDs and codes only: runtime/generation, connection/session ID, record type, operation, latency, byte counts, outcome. Do not log path content if paths may be sensitive, and never log file/terminal contents or credentials.

Any protocol extension requires synchronized updates to:

- `docs/workspace-stream-v1.md`;
- Python enums/validators;
- Worker session implementation;
- TypeScript constants/parser/types;
- unit, integration, and compatibility tests.

Version the protocol rather than changing existing semantics incompatibly.

## 13. Accessibility, usability, and failure behavior

- All toolbar/context actions must be keyboard reachable.
- Tree rows use correct tree/treeitem semantics, levels, expanded state, selected state, and roving focus or an equivalent accessible pattern.
- Tabs use tablist/tab/tabpanel semantics and support arrow-key navigation.
- Dialog focus is trapped, Escape cancels, initial focus is intentional, and focus returns to the invoking control.
- Status changes/errors use appropriate polite/assertive live regions without announcing every terminal byte.
- Never rely on color alone for dirty, connected, conflict, or failure states.
- Maintain readable contrast in the dark theme.
- Tooltips cannot be the only source of critical information.

Use toasts for transient success (“Saved train.py”), inline banners for connection-wide conditions, and localized row/tab messages for operation failures. Avoid one permanent red paragraph that accumulates unrelated errors.

Empty/loading/error states should tell the user what to do:

- runtime not editor-capable: return to details and start a new editor-capable generation;
- runtime starting: show its state and a controlled refresh/poll path;
- workspace disconnected: preserve dirty buffers and offer reconnect;
- file too large/binary: offer terminal-based tools and show the configured size limit;
- conflict: offer compare/reload/copy, never generic “save failed” only;
- live-only changes: persistently label them until a durable revision succeeds.

## 14. Testing plan

Do not rely only on TypeScript compilation.

### TypeScript protocol unit tests

Expand `UI/User/test/workspaceProtocol.test.ts` to cover:

- authentication and HELLO/READY handshake;
- invalid grant protocols;
- records split across multiple WebSocket messages;
- several records coalesced into one message;
- `PTY_OPENED` regression (must not disconnect);
- PTY stdout/exit and lifecycle enforcement;
- list/stat result;
- empty and multi-chunk reads;
- chunk sequence mismatch, wrong request ID, duplicate END;
- size and SHA-256 mismatch;
- streamed writes including empty file;
- request error rejects only that request;
- request timeout/cancel cleanup;
- close rejects all pending operations once;
- unknown/malformed record closes the connection;
- close-code preservation and stale socket fencing.

Build byte-level test helpers that encode/decode actual records rather than mocking high-level methods only.

### React component tests

Add focused tests using Vitest and Testing Library:

- recursive expand/collapse and pagination;
- opening files creates/switches models;
- edit marks only the correct tab dirty;
- Save and Save All use the correct model/version;
- edits made during an in-flight save remain dirty;
- conflict flow does not overwrite;
- dirty tab close and navigation guards;
- create/rename/delete validation and updates;
- terminal open/exited/restart controls;
- reconnect preserves dirty text;
- loading, read-only, disconnected, and fatal states;
- keyboard navigation and accessible names.

Monaco/Xterm need narrow wrappers or adapters so component tests can use controlled fakes without replacing transport behavior.

### Python unit/component tests

Extend Access and Worker tests for:

- valid PTY_OPENED lifecycle alongside file requests;
- file response and PTY output concurrency without corrupt framing;
- request limits and malformed records;
- recursive/nested paths and pagination;
- atomic write/version conflict;
- symlink/hardlink/device/FIFO rejection;
- rename/delete version checks;
- cleanup during active file request and PTY;
- missing `python3`/container exit/Docker exec failure mapping;
- exact workload user and root.

### End-to-end browser test

Add a Playwright-style real browser gate if the repository does not already have one. It must use the deployed protocol path, not intercept the WebSocket. At minimum:

1. create/start or use a fixture editor-capable workspace;
2. open the editor and wait for Connected;
3. expand a nested folder;
4. open a file and assert its content;
5. edit and save;
6. run `cat` on that file in the terminal and observe the edit;
7. create a file in the terminal, refresh explorer, open/edit it;
8. modify an open file in the terminal and prove a stale editor save yields conflict;
9. resize terminal and run an interactive command;
10. exit/restart terminal while explorer still works;
11. interrupt/reconnect Gateway and prove dirty buffer preservation;
12. stop runtime and verify the editor becomes unavailable without retry storms.

Run protocol tests under fragmented/coalesced TCP conditions. A localhost happy path can mask framing defects.

### Required commands/checks

Use the repository's real commands and supported Node version. At handoff, report exact commands and results. Expected categories include:

```bash
cd UI/User
npm run test:interactive
npm run build
npm run lint

cd ../../Access_Container
pytest

cd ../Worker
pytest test/unit/test_runtime_broker.py test/unit/test_file_service.py

cd ..
pytest test/interactive_e2e
```

Adjust paths to the actual working directory/test configuration. Do not claim a test passed if it was skipped due to missing Docker/browser/GPU; record it as an unverified deployment gate.

## 15. Durable save and training handoff (separate gated phase)

The live editor may ship before durable snapshots, but the product must be honest. “Save” writes to the running container overlay. Stopping/deleting that runtime can discard those changes.

Only enable “Save for Later” after completing the entire durable workflow already scaffolded by migration `003_workspace_editor_snapshots.sql` and the Scheduler services:

1. UI flushes all dirty editor files successfully.
2. UI sends a save request containing the exact runtime generation and parent revision ID with a stable idempotency key.
3. Scheduler verifies owner, READY editor runtime, generation, and single active save.
4. Worker receives a fenced, journaled command and captures the exact workload filesystem using a designed commit/export process.
5. Snapshot artifact upload uses private scoped capabilities and verified receipt/digest/size.
6. Builder imports/builds a new immutable `SNAPSHOT` revision and publishes a digest reference.
7. Scheduler advances `saved_revision_id` only after successful publication, without corrupting the previous revision on failure.
8. UI polls the operation and reports requested/capturing/building/ready/failed states accurately.
9. Runtime stop during save is fenced or delayed safely; retries are idempotent across Worker/Scheduler restarts.
10. Multi-host, Postgres, Docker, object-store, and failure-injection tests pass.

Only enable “Submit for Training” after it always derives a batch job from a completed immutable saved revision, not from a mutable live container. Preserve provenance: workspace, revision, save operation, image digest, command, and owner. Validate command/settings using the existing strict schema and show the created job link.

Until those gates pass:

- keep both feature flags `0`;
- keep controls hidden or clearly disabled with an honest explanation;
- retain a persistent “Live changes are temporary” indicator;
- never label a live file write as a saved revision.

## 16. Security invariants

These are acceptance requirements, not optional hardening:

- Scheduler owner authorization is required before every grant and durable operation.
- Grants are short-lived, single use, `Cache-Control: no-store`, and tied to resource, generation, service, user, and Gateway.
- Browser reconnect obtains a fresh grant.
- No secret is placed in URL query parameters, logs, localStorage, analytics, or error text.
- Origin enforcement and WSS/TLS stay enabled in production.
- Workspace paths remain relative and bounded; no traversal or alternate separators.
- Symlinks and special files remain rejected by browser file operations.
- No arbitrary shell command is built from file-operation values.
- Terminal freedom is confined to the assigned workload/container permissions; do not mount the Docker socket or expose Worker host paths.
- File and terminal traffic remains bounded with backpressure; no unbounded browser/server queues.
- Runtime generation/lease fencing is checked before and during access.
- On stop/revoke/lost assignment, new access is denied and active access closes in bounded time.
- Preserve terminal-only protocol behavior and compatibility tests.

Run a manual adversarial pass for `../`, absolute paths, backslashes, NUL, Unicode edge cases, enormous metadata, malformed lengths, symlink swaps, hardlinks, stale versions, duplicate IDs, chunk replay/reordering, rapid reconnect, repeated clicks, and stop-during-write.

## 17. Implementation sequence

Follow this order so visual work does not conceal transport defects:

### Phase A — stabilize the existing path

1. Reproduce and test the disconnect.
2. Handle `PTY_OPENED` and formalize PTY state.
3. Add record shape/digest validation, request cleanup, and close idempotency.
4. Verify list + read + PTY simultaneously against a real runtime.

### Phase B — sound frontend architecture

1. Extract protocol-facing hook/service and reducer.
2. Add Monaco model registry and correct dirty/save behavior.
3. Add connection fencing, error types, dirty guards, and reconnect foundation.
4. Keep the existing route functional after every change.

### Phase C — IDE user experience

1. Build shell/title/activity/explorer/editor/terminal/status layout.
2. Add recursive explorer and paging.
3. Add tabs, keyboard commands, save/conflict UX.
4. Add file/folder mutations and confirmations.
5. Add resizable/collapsible panes, responsive mode, polish, and accessibility.

### Phase D — backend robustness

1. Add broker outbound serialization and lifecycle tests.
2. Resolve concurrency claims and directory version behavior.
3. Add state/read-only/drain behavior and observability.
4. Run security and fault tests.

### Phase E — deployment acceptance

1. Pass frontend/Python tests and production builds.
2. Pass real WSS/browser/container E2E through Scheduler, Gateway, Access, and Worker.
3. Verify proxy WebSocket configuration and exact allowed origins.
4. Enable editor admission for new runtime generations only.
5. Monitor connection outcomes, protocol errors, latency, and resource use.
6. Keep durable-save/training flags disabled unless section 15 is complete.

## 18. Definition of done and handoff checklist

Before declaring the editor complete, provide:

- a concise architecture/change summary;
- screenshots at desktop and narrow widths;
- the exact disconnect root cause and regression test;
- supported file types/size/page limits and terminal constraints;
- live-save versus durable-save behavior;
- all test/build/lint commands with pass/fail/skip counts;
- a real end-to-end test record through public WSS;
- configuration/feature flags changed;
- database/protocol migrations, if any;
- deployment and rollback steps that preserve existing runtimes/state;
- known limitations and deliberately deferred features;
- security review notes confirming the invariants above.

The work is not done if files merely render, if a terminal merely prints a canned prompt, if errors are swallowed, if refresh loses unsaved browser text without warning, if ordinary PTY messages disconnect the workspace, or if “Save” implies durability that the system does not provide.

## 19. Explicit non-goals for the initial release

Unless separately requested, do not delay the first solid release for:

- extensions marketplace;
- remote VS Code protocol compatibility;
- collaborative multi-user editing;
- full language servers, debugging adapters, notebooks, or Git GUI;
- arbitrary binary/hex editing;
- multiple simultaneous PTYs without protocol PTY IDs;
- browser-side package installation abstractions (users already have the terminal);
- exposing host networking, Docker API, or Worker filesystem;
- durable snapshots/training submission before their full gated pipeline is complete.

A minimal IDE that never loses work silently, respects runtime boundaries, and reliably combines Monaco with a real PTY is preferable to a broad but fragile VS Code imitation.
