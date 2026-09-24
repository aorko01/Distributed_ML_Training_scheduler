# Active plan: usable interactive development workspaces

Status: implementation handoff only. This plan does not claim the feature is implemented.

Prepared against the repository on 2026-09-24. Reinspect the checkout and preserve unrelated work before editing. This plan replaces the earlier capacity-flow handoff for the package-installation feature.

## 1. Scope and acceptance contract

The user must be able to open a ready interactive workspace, edit code in the browser, install ordinary Python and Ubuntu/Debian packages from its terminal, and run that code on the assigned GPU. The commands below must work in a newly built, operator-enabled development workspace without special pip flags or a manual virtual-environment activation step:

```sh
id
pwd                              # /workspace
python -c 'import torch; print(torch.cuda.is_available())'
pip install six
python -c 'import six; print(six.__version__)'
sudo -n apt-get update
sudo -n apt-get install -y ffmpeg
ffmpeg -version
python train.py
```

An editor change saved to the **live** container must be visible to `python train.py` immediately. Packages and code must still be there after closing and reopening the browser terminal while the same runtime is alive. Installation must use the workload container, never the Access container or Worker host.

This phase stops at a functioning *live* development runtime. Do not implement Docker commit, snapshots, Save for Later, image publication, or training submission here. The current UI must continue to call live edits **live-only** and must not imply that Stop preserves them. Section 10 lists only the layout and image contracts that make a later persistence project straightforward.

“Install packages” means normal `pip` packages, `apt` packages, and their build dependencies within the container. Kernel modules, a full init system, host-level Docker, host mounts, and arbitrary GPU driver changes are outside the container contract. A user may install a package that breaks their own runtime; the platform must still fence and clean up that runtime correctly.

## 2. Current code and why it fails

- `Docker_Image_Builder/interactive_build.py::dockerfile()` installs uploaded requirements with whichever system `pip` the base image supplies, then forces `USER 10001:10001`. It creates neither a usable virtual environment nor `sudo`.
- `Worker/interactive/docker_ops.py::workload()` launches that user with `cap_drop=["ALL"]` and `no-new-privileges:true`. Merely adding `sudo` to the image, or setting `INTERACTIVE_ALLOW_ROOT=1`, cannot make `sudo apt` work under these launch settings.
- `Worker/interactive/broker.py::DockerSession` launches `/bin/sh` as the inspected image user but overrides `HOME=/tmp`. This sends user installs and caches to a surprising location and hides the prepared account's home.
- `Worker/interactive/file_service.py::FileService` runs its fixed editor helper with `python3` from the image `PATH`. A user-writable Python environment could therefore accidentally become an editor dependency; pin the helper to a verified, image-owned interpreter.
- Workload egress is off by default. `INTERACTIVE_INTERNET_ENABLED=1` on the Scheduler **and** `INTERACTIVE_ALLOW_INTERNET=1` on the selected Worker are required for a new runtime to use Docker's bridge network. The browser cannot enable either policy.
- `Scheduler/.env.runtime.example` has `WORKSPACE_EDITOR_ENABLED=1`, `INTERACTIVE_INTERNET_ENABLED=0`, and `WORKSPACE_SAVE_ENABLED=0`. The current editor's “online” badge reflects the Scheduler launch hint, so check its claim against the Worker’s actual network mode before treating it as proof of connectivity.

The old `INTERACTIVE_ACCESS_FLOW.md` describes a different SSH/root design. Use the live Docker-exec broker, image builder, and Worker launch code named above as the implementation source of truth.

## 3. Chosen model and trust boundary

Keep the workload's normal image user as `10001:10001` (`dml`) and make `/workspace` and the Python environment writable by that user. Install `sudo` in the image with one root-owned, mode `0440` sudoers entry granting `dml` passwordless commands. The browser terminal remains an ordinary user's shell; `sudo apt-get ...` provides the familiar machine-like workflow.

Give *only the workload* Docker's normal restricted capability set and permit setuid escalation inside that container when development mode is enabled. Do this by omitting `cap_drop=["ALL"]` and `no-new-privileges:true` for that workload mode. Do **not** use `privileged=True`, `cap_add=["ALL"]`, `userns_mode="host"`, host networking, host directory/Docker-socket mounts, or extra GPU devices. Keep Docker's default seccomp/AppArmor profile, the selected GPU UUID, quotas, PID/memory/CPU limits, labels, leases, and exact-ID cleanup. Keep Access and Tailscale sidecars' existing non-root, read-only, capability-dropped settings unchanged.

This mode gives users root **inside a shared-kernel container** through `sudo`. It is appropriate only when the operator accepts that trust model. Make it an explicit operator opt-in, default off, enforced independently at Scheduler and Worker; never accept a browser-supplied privilege flag. The existing strict runtime path must keep its current controls when the opt-in is off.

Use these configuration names consistently, or choose equivalent names once and document the mapping:

```text
Scheduler: INTERACTIVE_DEVELOPER_MODE_ENABLED=0
Worker:    INTERACTIVE_ALLOW_DEVELOPER_MODE=0

Existing, separate network gates:
Scheduler: INTERACTIVE_INTERNET_ENABLED=0
Worker:    INTERACTIVE_ALLOW_INTERNET=0
```

For a runtime advertised as fully package-capable, require all four gates to be enabled. If the Worker cannot honor the pinned developer/network policy, return a stable policy/capability failure before starting the workload; do not silently present an offline or sudo-disabled runtime as ready. The Scheduler should avoid scheduling a developer-mode runtime onto a Worker that has not advertised that capability, while the Worker's local check remains authoritative.

## 4. Immutable image and runtime contract

Add a server-owned `developer_mode` boolean to the immutable interactive launch spec. Pin it when the runtime is created and never derive it from a request body. Retrying an existing idempotency key must return the same pinned runtime even if operator policy has changed meanwhile; keep the hash based on the canonical client request. Keep the existing `allow_root` meaning separate: developer mode uses image user `10001`, with controlled `sudo` inside the workload.

The builder must identify which revisions have the prepared developer image profile. Add a versioned image label such as `io.dml.developer-profile=v1`, inspect the resulting image, and report that profile in the trusted Builder ready callback. Persist the result in existing revision metadata or an additive field; the Scheduler must only pin `developer_mode=true` for a revision reported as prepared. At pull time, the Worker independently verifies the image's digest, configured user/workdir, and developer-profile label before applying the relaxed workload launch settings. Do not infer readiness from a mutable tag or merely from the Scheduler flag.

Older revisions without this profile remain eligible for the existing strict runtime mode. Do not mutate their immutable image or silently switch their runtime to developer mode. Show an actionable “create a new workspace image to enable package installation” message where relevant. A new workspace derived from an existing job should also be rebuilt into the prepared profile; qualify the source image as described in section 5.

The image configuration, rather than a one-off shell command, must contain:

```text
User:       10001:10001
WorkingDir: /workspace
HOME:       /home/dml
VIRTUAL_ENV:/opt/dml-venv
PATH:       /opt/dml-venv/bin:<existing base-image PATH>
```

The image must not declare `/workspace`, `/opt/dml-venv`, or `/home/dml` as Docker volumes or mount them at runtime. They are ordinary paths in the workload's writable filesystem. This is a forward-compatibility requirement, not a request to implement persistence in this phase.

## 5. Prepare the development image in the Builder

Update `Docker_Image_Builder/interactive_build.py::dockerfile()` and focused builder tests. Preserve digest-pinned `FROM`, archive validation, immutable tags, and build cancellation. Build in this order:

1. Switch to `USER root` for image preparation. Establish `/workspace`, the `dml` account with UID/GID 10001 and a usable shell, and `/home/dml` with correct ownership.
2. Qualify the base for this profile: it needs `/bin/sh`, a usable Python interpreter, a Debian/Ubuntu `apt` installation for OS-package management, and an image-owned Python interpreter for the editor helper. A source image that cannot support these requirements must fail at build time with a stable, user-facing reason, rather than producing a READY image with broken tools. Do not run untrusted `Dockerfile` content from the upload.
3. Install the minimal bootstrap packages as root during the image build: `sudo`, the matching `python3-venv`/`ensurepip` support where necessary, `ca-certificates`, and ordinary build tools needed by common pip packages (`build-essential`, `pkg-config`, `git`, `curl`). Use noninteractive apt, `--no-install-recommends`, and remove apt index files from the built layer after installation. Resolve the venv package against the Python actually selected for the workspace; fail clearly if that interpreter cannot create a working venv.
4. Create `/opt/dml-venv` using that Python with `--system-site-packages`, so the selected PyTorch/CUDA stack remains importable. Make the environment owned by UID/GID 10001. Set `VIRTUAL_ENV` and prepend its `bin` directory to image `PATH` with Dockerfile `ENV`, not an activation script. Run smoke checks for `python`, `python3`, `pip`, `torch`, and the expected CUDA bindings before publishing the image.
5. For an uploaded workspace, copy the project and install `requirements.txt` with `/opt/dml-venv/bin/python -m pip install -r /workspace/requirements.txt`. Do not run bare system `pip`; PEP 668 base images may reject it. If the user did not upload files, the placeholder empty requirements file must still allow creation of an empty development workspace.
6. Configure a root-owned `/etc/sudoers.d/dml` granting `dml` `NOPASSWD:ALL`; set mode `0440` and validate with `visudo -cf` during build. Do not expose registry, object-store, Worker, or management credentials in the workload image or environment.
7. Chown `/workspace`, `/opt/dml-venv`, and `/home/dml` to 10001. Switch the final image back to `USER 10001:10001`, retain `WORKDIR /workspace`, and apply the versioned developer-profile label.

Choose one Python executable as the workspace default and test it across representative official PyTorch tags and from-job sources. In particular, `python -c 'import torch'` must succeed *inside the venv*, and an imported package installed later must be visible to the same `python` used by `python train.py`. Do not patch PEP 668 away globally or set `PIP_BREAK_SYSTEM_PACKAGES=1` as the product solution.

For package commands, document `pip install <name>` and `sudo apt-get install <name>`. Discourage `sudo pip install`: it bypasses the user-owned environment and can damage the Python stack. The shell may allow it because the user has sudo, but product guidance should point to the consistent interpreter.

## 6. Workload launch and policy enforcement

Update `Scheduler/app/services/scheduling/config.py` and the runtime admission path to pin `developer_mode` from operator policy plus the ready revision's reported profile. Include this server-owned field in the immutable launch spec and runtime public capability response. Ensure the browser's resource-requirements object still cannot set it.

Update Worker readiness/inventory and `Worker/interactive/docker_ops.py` so the Scheduler can place developer-mode work on capable Workers and the Worker rechecks its local `INTERACTIVE_ALLOW_DEVELOPER_MODE` and `INTERACTIVE_ALLOW_INTERNET` gates. Reject a mismatch with a stable error before creating any workload. The Worker's image pull validation must reject a `developer_mode=true` spec unless the inspected digest has the expected profile label, UID 10001, and `/workspace` workdir.

After creating the workload but before publishing READY, extend `Worker/interactive/manager.py`'s existing broker smoke check to execute `id -u`, `sudo -n id -u`, `python -m pip --version`, a PyTorch import, and the fixed editor helper interpreter. Expect the shell user to be 10001 and sudo's target user to be 0. Fail and clean up through the existing fenced startup path if these checks fail; do not leave a superficially READY runtime whose package tools are broken. Keep the checks local to the workload; external package downloads belong in the end-to-end acceptance test, not on every startup.

Construct workload Docker options explicitly for both modes:

| Option | Existing strict mode | Operator-enabled developer mode |
| --- | --- | --- |
| Configured user | Image user | Image user `10001:10001` |
| Capabilities | Drop all | Docker default restricted set |
| `no-new-privileges` | Enabled | Omitted so `sudo` setuid can work |
| Network | Existing pinned policy | Default bridge, only after both internet gates |
| Privileged/host mounts/host net | Never | Never |
| GPU, CPU, RAM, PID, disk, labels, leases | Existing limits | Same limits |

Do not mutate the Access or sidecar containers. Preserve preflight, exact label checks, cancellation fences, terminal close behavior, and cleanup. Because apt and pip can consume substantial writable storage, confirm the user-selected disk quota applies to `/var/lib/dpkg`, `/var/cache/apt`, `/opt/dml-venv`, `/home/dml`, and `/workspace`. Report disk exhaustion as a package/runtime error without allowing host disk exhaustion.

## 7. Terminal, editor, and shell behavior

Update `Worker/interactive/broker.py` so Docker exec inherits the prepared image's `PATH` and `VIRTUAL_ENV`, uses `HOME=/home/dml` (or the validated account home), remains in `/workspace`, and continues to run as the inspected image user. Keep PTY dimensions, cgroup identity checks, process termination, and the one-shell-per-connection behavior. Do not insert a shell wrapper that changes the process identity or defeats terminal cleanup.

Pin `Worker/interactive/file_service.py` to an image-owned interpreter outside `/opt/dml-venv`, so ordinary user `pip` changes cannot remove its dependencies. Validate that this interpreter exists before a runtime becomes READY. Keep the file helper running as the workspace user and rooted at the Worker-selected `/workspace`; terminal sudo must not grant the browser file API arbitrary host paths or Docker commands.

Check the editor UI in `UI/User/src/features/workspace/` and the runtime public response. Show an accurate package-capability hint only for a ready, verified developer runtime. Distinguish “network configured for this runtime” from a proven internet connection if no external connectivity probe is performed. Provide a short terminal help example for `pip install`, `sudo apt-get install`, and `python train.py`. Preserve clear **live-only** messaging. Keep Save for Later and Submit for Training disabled according to their actual backend feature flags; `editor_capable` alone must not enable those buttons. Do not implement either feature in this phase.

Verify both the terminal-only compatibility path and the browser workspace editor path. An install in either PTY must affect the *same workload* that runs the user's code and serves `/workspace` in the editor. A reconnect must create a new shell without resetting packages, the venv, the user home, or saved live files.

### File-by-file implementation map

| Area | Inspect and change |
| --- | --- |
| Builder image recipe and publication | `Docker_Image_Builder/interactive_build.py`, `Docker_Image_Builder/interactive_api.py`, `Docker_Image_Builder/test/unit/test_interactive_build.py` |
| Trusted image capability callback | `Scheduler/app/schemas/interactive_workspace_schema.py`, `Scheduler/app/api/interactive_workspace_route.py`, `Scheduler/app/services/interactive_workspace_service.py` |
| Runtime policy and placement | `Scheduler/app/services/scheduling/config.py`, `Scheduler/app/services/interactive_runtime_service.py`, `Scheduler/app/services/scheduling/policy.py`, `Scheduler/app/services/interactive_capacity_service.py` |
| Worker advertisement and launch | `Worker/hardware.py`, `Worker/managed_worker.py`, `Worker/interactive/docker_ops.py`, `Worker/interactive/manager.py` |
| Shell and editor execution | `Worker/interactive/broker.py`, `Worker/interactive/file_service.py`, `Worker/interactive/workspace_broker.py` |
| User feedback | `UI/User/src/features/workspace/WorkspaceIDE.tsx`, `UI/User/src/features/workspace/components/IDETitleBar.tsx`, `UI/User/src/services/interactive.ts` |
| Deployment examples and focused tests | `Scheduler/.env.runtime.example`, `Worker/.env.example`, `docs/interactive-runtime-operations.md`, relevant `Scheduler/test`, `Worker/test`, `Docker_Image_Builder/test`, and `test/interactive_e2e` cases |

## 8. Tests to add or update

### Builder unit and image tests

- Assert the generated Dockerfile uses the selected base interpreter, installs bootstrap packages, creates the venv, installs uploaded requirements into it, validates sudoers, and ends with `USER 10001:10001` plus the versioned profile label. Cover both upload and existing-job origins and an empty upload.
- Assert no bare `RUN pip install` against the system interpreter, no global `PIP_BREAK_SYSTEM_PACKAGES`, and no credentials, Access agent, host mount, or Docker socket in the generated workload image.
- Build at least one real official PyTorch/CUDA image in an integration gate. Run `python -c 'import torch; print(torch.__version__)'`, `pip --version`, `sudo -n true`, and `python -m pip install six`; verify the same `python` imports `six`. Exercise a Debian/Ubuntu Python 3.12/PEP 668 case to catch the reported failure. Validate an unsupported base fails before READY.

### Scheduler and Worker tests

- Scheduler: developer mode defaults off; browser-supplied `developer_mode`, internet, root, or Worker identity fields are rejected; the profile is pinned into a new runtime, and idempotent retries retain the original profile after policy changes; old revisions retain strict mode; an unprepared revision is never advertised as package-capable.
- Scheduler placement: a developer runtime chooses only a Worker advertising the required capability; a mismatched Worker remains ineligible; no privilege policy is supplied by the user-selected CPU/RAM/disk form.
- Worker: strict mode still has `cap_drop=["ALL"]` and `no-new-privileges`; developer mode omits those two options but still has no privileged flag, host mounts, Docker socket, host networking, published ports, or extra GPU devices. A missing local gate, missing internet gate, mismatched image label, wrong UID, or wrong workdir fails before workload start.
- Broker and file helper: shell is non-root with `/workspace`, `/home/dml`, venv `PATH`; `sudo -n` works only in the configured developer image; file operations still execute as the image user through the image-owned helper interpreter. Terminal close/reopen keeps workload filesystem state.

### End-to-end acceptance on a provisioned GPU Worker

Start a fresh developer workspace through the browser. Save `train.py` in the editor, run it in the terminal, edit it again, and confirm output changes immediately. Run `pip install six`, import it, run `sudo -n apt-get update`, install a small OS package, and invoke its binary. Close/reopen the terminal and repeat both checks. Verify PyTorch still imports and `torch.cuda.is_available()` reflects the assigned GPU. Verify the command cannot see a Docker socket or Worker host mounts. Stop the runtime and confirm exact-ID cleanup and lease release work normally; do not claim that this phase preserves stopped-runtime changes.

Repeat the negative path with developer mode disabled: no `sudo` escalation, strict Docker options, no misleading “package-capable” UI, and no regression in ordinary editor/terminal access. Test a Worker with internet disabled to ensure it never reports a fully package-capable runtime as READY. Run focused Builder, Scheduler, Worker, Access, and UI tests plus the existing interactive end-to-end suite; record exact commands and results in the implementation report.

## 9. Delivery order and rollout

1. Add tests for the failing current behavior, then implement and qualify the Builder's prepared image profile. Do not roll out the Worker's relaxed launch policy before the image profile can be independently verified.
2. Add revision capability reporting and Scheduler/Worker policy gates. Keep both new flags default `0`; update `Scheduler/.env.runtime.example`, `Worker/.env.example`, and `docs/interactive-runtime-operations.md` with the new settings and the user command examples.
3. Fix broker home/environment and pin the file helper's interpreter. Run strict-mode regressions before enabling the new path.
4. Add accurate UI capability text, then perform real-image and real-GPU browser acceptance. Build and publish new workspace revisions for this profile; old immutable revisions keep their old behavior.
5. Enable both developer-mode flags and both internet flags only on a deployment that has passed these checks. Restart Scheduler and Workers, then create a **new** runtime. Roll back by disabling admission of new developer runtimes and letting current assignments stop through the normal fenced path; do not rewrite or delete existing images.

## 10. Forward-compatibility contracts only

Keep the user's edited `/workspace` files, `/opt/dml-venv`, `/home/dml`, and OS package changes inside the workload filesystem, with no Docker volumes at those paths. Keep the image's `PATH`, `VIRTUAL_ENV`, user, and workdir consistent so a later process launched from that image uses the same Python environment. Preserve immutable image digests, labels, and revision provenance. These are design constraints for later work; **do not implement capture, publication, durable Save, or training in this task**.
