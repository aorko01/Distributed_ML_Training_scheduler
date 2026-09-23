# Interactive access features

This document describes interactive access from a user's point of view. It also
explains what an operator can change through `Scheduler/.env.runtime` without
going into implementation details.

The repository currently contains `Scheduler/.env.runtime.example`, not a real
deployment-specific `.env.runtime`. The values below therefore describe the
checked-in example. A deployed environment may use different values.

## Current checked-in setup

| Area | Example value | What a user experiences |
| --- | --- | --- |
| New Worker assignments | Off | No newly managed batch or interactive work is assigned to Workers. |
| Interactive runtime admission | Off | A workspace image can be created, but a new live runtime cannot be started. |
| Browser workspace editor | On for new runtimes | Once runtime admission is enabled, newly started runtimes use the browser IDE. Existing terminal-only runtimes are not upgraded in place. |
| Internet inside the workload | Off | `pip install`, `apt`, `curl`, `git clone`, and online dataset downloads fail. Local files and already-installed packages still work. |
| Maximum runtime lifetime | 600 seconds | A placed runtime has a ten-minute Scheduler deadline. The Worker must be configured with a matching limit to avoid an earlier local stop. |
| Save for Later | Off | Saving a file only writes it to the live container. It does not make the whole workspace durable. |
| Submit for Training | Off | A live workspace cannot be handed directly to the training queue. |

In short, the example is a safe rollout configuration: users can create and
build workspace images, but interactive execution remains closed until the two
admission switches are enabled.

## What users can do

### 1. Create and manage a workspace image

Users can:

- create a workspace from a ZIP upload;
- select a PyTorch and CUDA base image for an uploaded workspace;
- create a workspace from one of their existing jobs that already has an image;
- see the workspace build state and build logs;
- cancel a build while it is queued or building;
- list their workspaces and reopen a workspace details page later.

An uploaded ZIP must contain `requirements.txt` and can be up to 64 MiB. The
builder produces an immutable image revision. Building a workspace image is
separate from starting a live runtime.

### 2. Start, monitor, and stop a temporary runtime

When runtime admission is enabled, a user can start an image-ready workspace.
The UI shows the runtime moving through states such as queued, pulling,
starting, connecting, ready, stopping, stopped, or failed.

The Scheduler chooses the Worker and GPU. Users cannot choose a host, container,
GPU identifier, Docker option, network mode, or hidden service credential.

Only one active runtime is allowed for a workspace. A runtime can wait in the
queue when no compatible Worker is free. The default scheduling order is:

1. VRAM estimation work;
2. interactive runtimes;
3. retried training jobs;
4. normal training jobs.

An interactive runtime reserves its Worker exclusively while it is active.
Even on a multi-GPU Worker, all GPUs must be idle before placement.

Users can stop the runtime from its details page or from the browser IDE. A
configured lifetime deadline can also stop it automatically. Stopping normally
discards changes made only in the live container.

### 3. Connect securely from a browser

The details page provides a connection check when a runtime is ready. An
editor-capable runtime can then be opened in the browser IDE.

Access is owner-only and uses a fresh, short-lived, single-use connection grant.
The browser does not receive Worker addresses, container IDs, host paths, or
private network credentials.

If the connection drops, the IDE retries with a fresh grant up to five times.
Dirty editor buffers stay in browser memory during reconnection. The terminal
process itself is not recoverable after a disconnect, so the IDE starts a new
shell after reconnecting.

### 4. Browse and manage project files

The Explorer is rooted at the live workload's configured working directory.
Users can:

- browse folders recursively;
- expand, collapse, and refresh folders;
- create files and folders;
- rename files and folders;
- delete files and empty folders;
- copy a relative path;
- use the keyboard to move through the tree.

The Explorer refreshes after terminal activity settles, when the terminal
exits, every 15 seconds, and when the browser regains focus. This makes files
created or changed from the terminal appear in the editor.

The editor accepts UTF-8 text files up to 2 MiB. Binary files, larger files,
symlinks, devices, sockets, FIFOs, and unsafe hard links cannot be edited.

### 5. Edit files in a browser IDE

The IDE provides:

- a Monaco-based code editor with a local fallback editor;
- multiple file tabs with independent unsaved state;
- undo and redo through the editor;
- word-wrap and minimap toggles;
- resizable Explorer and terminal panels;
- a compact layout for narrow screens;
- connection, terminal, cursor, read-only, and unsaved-state indicators;
- remembered layout preferences in the browser.

Useful shortcuts are:

| Shortcut | Action |
| --- | --- |
| `Ctrl/Cmd+S` | Save the active file to the live container |
| `Ctrl/Cmd+Shift+S` | Save all changed files to the live container |
| `Ctrl/Cmd+W` | Close the active tab |
| `Ctrl/Cmd+B` | Show or hide the Explorer |
| `Ctrl + backtick` | Show or hide the terminal |

The UI warns before closing a dirty tab, leaving the editor, refreshing the
page, or stopping a runtime with unsaved files.

File saving uses a version check. If the terminal, another editor, or another
process changes the same file, the IDE preserves the user's local text and
reports a conflict instead of silently overwriting the newer file. The user can
keep editing, copy the local text, or reload the server version.

### 6. Use a real terminal in the same runtime

The terminal runs in the same workload container, as the same image user and in
the same working directory as the file editor. Changes made by terminal commands
are therefore visible to the Explorer and editor.

Users can:

- run normal shell commands and interactive terminal programs;
- see ANSI colours and streaming output;
- resize, collapse, expand, or maximize the terminal;
- clear the displayed output;
- close only the shell while keeping file access connected;
- start a fresh shell after the previous shell exits.

There is one terminal process per browser workspace connection. Pasting multiple
lines requires confirmation.

### 7. Understand live and durable changes

There are two different kinds of saving:

- **Save / Save All** writes edited files into the currently running container.
  These changes are live but temporary.
- **Save for Later** is the separately gated snapshot workflow intended to
  capture code, files, and packages installed into the container's writable
  layer as a new immutable workspace revision.

With the checked-in example, only live file saving is enabled. If a Save for
Later control is visible, the backend still rejects the request while
`WORKSPACE_SAVE_ENABLED=0`.

The repository contains foundations for snapshot capture and publication, but
the flag should only be enabled after the Worker, object storage, Builder, and
multi-host acceptance checks for that deployment have passed. Changing the flag
alone is not a substitute for deploying and validating the complete pipeline.

### 8. Submit a saved workspace for training

The gated training handoff is intended to snapshot the workspace and create a
normal training job from the resulting immutable image. This allows editor
changes and packages installed in the terminal to become part of the training
environment.

The current UI submits the default command `python train.py`. This feature
requires durable saving and is off in the checked-in example. It should not be
enabled until a completed snapshot reliably becomes exactly one traceable batch
job and the end-to-end training path has passed deployment acceptance.

## Features controlled by `.env.runtime`

Changes to this file require the Scheduler service to be restarted. Admission
flags affect new work. Editor mode, internet capability, and the resource profile
are pinned when a new runtime generation is created; they do not retrofit an
already-running runtime.

### Admission and product features

| Variable | Example | User-visible effect |
| --- | ---: | --- |
| `WORKER_NEW_WORK_ENABLED` | `0` | Master switch for new managed Worker assignments. With `0`, new interactive and managed batch work cannot be placed. |
| `INTERACTIVE_RUNTIME_ENABLED` | `0` | Controls whether users can start interactive runtimes and whether those runtimes can be placed. Both admission switches must be `1` for a workspace to become usable. |
| `WORKSPACE_EDITOR_ENABLED` | `1` | New runtimes receive the full files-plus-terminal browser IDE. With `0`, new runtimes remain terminal-only compatibility runtimes and the editor page is unavailable. |
| `INTERACTIVE_INTERNET_ENABLED` | `0` | Advertises internet access for new workloads. It must also be enabled on the selected Worker with `INTERACTIVE_ALLOW_INTERNET=1`; otherwise the Worker fails closed to offline mode. Users cannot turn this on themselves. |
| `WORKSPACE_SAVE_ENABLED` | `0` | Accepts durable Save for Later requests. Enable only after the full snapshot pipeline is deployed and accepted. |
| `WORKSPACE_TRAINING_SUBMISSION_ENABLED` | `0` | Accepts Submit for Training requests. It also requires `WORKSPACE_SAVE_ENABLED=1` and a complete training handoff pipeline. |

### Time limits and failure tolerance

| Variable | Example | User-visible effect |
| --- | ---: | --- |
| `INTERACTIVE_STARTUP_SECONDS` | `1920` | Maximum time allowed for an assigned runtime to pull its image and become ready. A smaller value fails slow starts sooner; a larger value lets large images and slower networks finish. |
| `INTERACTIVE_LIFETIME_SECONDS` | `600` | Scheduler-side maximum lifetime after assignment. `600` gives about ten minutes; `0` disables this Scheduler limit. The Worker has its own `INTERACTIVE_MAX_DURATION_SECONDS`, which must be tuned separately. The earlier limit wins. |
| `WORKER_ASSIGNMENT_LEASE_SECONDS` | `45` | How long a Worker can go without a successful renewal before its assignment is treated as unsafe. This affects tolerance of short network interruptions, not the planned session length. Values below 20 are rejected. |

### Runtime size and placement profile

Users do not select these values. Every new runtime receives the single profile
chosen by the operator.

| Variable | Example | User-visible effect |
| --- | ---: | --- |
| `INTERACTIVE_PROFILE_VERSION` | `gpu-v1` | Names the profile shown on runtime records. Changing it helps distinguish a new operator policy. |
| `INTERACTIVE_PLATFORM` | `linux/amd64` | Limits placement to matching Workers. Supported values are `linux/amd64` and `linux/arm64`. The workspace image must match. |
| `INTERACTIVE_CPU` | `2` | CPU allocation for the workload. A Worker also needs one spare CPU core to qualify. |
| `INTERACTIVE_RAM_GB` | `8` | Memory allocation for the workload. A Worker also needs 1 GiB of spare RAM to qualify. |
| `INTERACTIVE_PIDS` | `256` | Maximum number of processes in the workload. Valid values are 16 through 4096. Too small a value can prevent multiprocessing or data-loader workers from starting. |
| `INTERACTIVE_WRITABLE_GB` | `20` | Maximum writable container storage for code, generated files, caches, and installed packages. The Worker must support storage quotas. |
| `INTERACTIVE_PULL_HEADROOM_GB` | `40` | Extra free disk required for pulling image layers. Increasing it reduces the chance of a disk-full pull but also reduces the number of eligible Workers. |
| `INTERACTIVE_MIN_VRAM_GB` | `4` | Minimum memory required on the selected GPU. Raising it sends users only to larger GPUs and can increase queue time. |
| `INTERACTIVE_GPU_MODELS` | empty | Optional comma-separated exact GPU model allowlist. Empty permits any model that meets the VRAM requirement. A restrictive list can leave a runtime queued indefinitely. |
| `INTERACTIVE_ALLOW_ROOT` | `0` | With `0`, images whose configured user is empty or root are rejected. With `1`, root-based workspace images may run; this weakens workload isolation expectations and should be deliberate. |

The Scheduler also requires a compatible NVIDIA runtime, supported disk quotas,
enough free resources, and an idle GPU. Increasing CPU, RAM, disk, VRAM, or model
requirements improves the runtime profile but usually increases queue time.

### Scheduling policy

| Variable | Example | User-visible effect |
| --- | --- | --- |
| `SCHEDULING_POLICY` | `three-tier` | Selects the placement policy. The current code supports only `three-tier`; another value prevents policy startup rather than enabling a different user feature. |

### Access and service connectivity

These settings do not add buttons, but incorrect values make runtime placement
or browser connection unavailable.

| Variable | Purpose and user impact |
| --- | --- |
| `WORKER_CREDENTIALS_FILE` | Protected Worker identity map. Missing or incorrect credentials make Workers unavailable, so jobs and runtimes remain unassigned. |
| `INTERACTIVE_MANAGEMENT_URL` | Private management service used to prepare and authorize runtime access. If unreachable, runtimes cannot finish connection setup. |
| `INTERACTIVE_MANAGEMENT_CA_FILE` | CA used to verify that private management service. A wrong CA causes secure connection setup to fail. It can be omitted when the service uses a publicly trusted CA. |
| `INTERACTIVE_CONTROLLER_SECRET_FILE` | Protected Scheduler-to-management credential. If unavailable or invalid, access setup fails. |
| `INTERACTIVE_GATEWAY_WSS_ORIGIN` | Public `wss://` address used by browsers. It must be reachable from the user's browser and cannot contain credentials, a query, or a custom path. |
| `INTERACTIVE_GATEWAY_ID` | Selects the authorized Gateway identity. A mismatch prevents connection grants from working. |
| `INTERACTIVE_BUILDER_SECRET_FILE` | Authenticates the interactive image Builder. If it is wrong or unavailable, new workspace revisions cannot be built or published. |

Secrets and private credentials should be stored in protected files. They should
not be placed directly in `.env.runtime` values or exposed to the browser.

## Common configurations

### Live browser IDE, offline and temporary

Set:

```ini
WORKER_NEW_WORK_ENABLED=1
INTERACTIVE_RUNTIME_ENABLED=1
WORKSPACE_EDITOR_ENABLED=1
INTERACTIVE_INTERNET_ENABLED=0
WORKSPACE_SAVE_ENABLED=0
WORKSPACE_TRAINING_SUBMISSION_ENABLED=0
```

Users get the Explorer, editor, and terminal, but online installs do not work and
all live changes disappear when the runtime is destroyed.

### Live browser IDE with internet access

Set `INTERACTIVE_INTERNET_ENABLED=1` on the Scheduler and
`INTERACTIVE_ALLOW_INTERNET=1` on each eligible Worker, then start a new runtime.
Existing runtimes keep their original network mode.

Internet access changes only the workload network. It does not make the
container privileged, expose Worker ports, or give the browser control over
network policy.

### Durable workspace snapshots

After the complete snapshot pipeline has been deployed and accepted, set
`WORKSPACE_SAVE_ENABLED=1`. A successful Save for Later should produce a new
immutable revision containing live code, terminal-created files, and packages
installed in the writable layer. Running processes, GPU memory, and temporary
environment variables are not restored into a later runtime.

Editing `requirements.txt` alone does not install anything. The package must be
installed in the terminal before the snapshot if the saved image is expected to
contain it.

### Training handoff

After durable saving and the snapshot-to-training path have both passed
acceptance, set:

```ini
WORKSPACE_SAVE_ENABLED=1
WORKSPACE_TRAINING_SUBMISSION_ENABLED=1
```

Submission must always derive the training job from the completed immutable
revision, never directly from a mutable live container.

## Important limitations

- The editor handles text files, not binary assets or files larger than 2 MiB.
- Only empty directories can be deleted through the Explorer.
- Each browser connection has one shell; a reconnect starts a new shell.
- There is no notebook UI, browser preview for workload HTTP applications, or
  user-controlled port publishing in the current interactive workspace.
- Normal Save is not durable. Stop or automatic timeout can discard it.
- A Scheduler flag cannot compensate for missing Worker, Gateway, management,
  Builder, object-store, registry, GPU, quota, DNS, or TLS configuration.
- Existing runtimes keep their original editor, network, and resource profile.
  Start a new runtime generation to receive changed settings.
