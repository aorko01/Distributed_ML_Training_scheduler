# Fix: Interactive session must behave like a normal VM

## Problem

When a user SSHs into an interactive session (via gateway -> access container ->
nsenter -> env container), they land in a shell that:

- Has a broken/stripped `PATH` (no conda/python even though the env image has it)
- Has wrong `HOME` (sshd sets `/home/sandbox`; the env container's real env lives
  under `/root` or the image's `ENV`)
- `sudo` "doesn't work" (nsenter runs the shell as `euid 0` via setuid, so sudo
  either isn't installed or refuses "you are already root")
- Cannot install packages / use apt / use the submitted `requirements.txt`
  environment the way a normal VM would

## Root cause

The access container joins the env container's **namespaces** via
`nsenter -t 1 -m -u -i -n -p` (shared PID namespace), but `nsenter` does NOT
inherit the env container's **environment**:

- `PATH` / `HOME` / `CONDA_PREFIX` set by the image's `ENV` or a `docker run`
  are only applied to processes Docker launches. `nsenter` + sshd's
  `ForceCommand` launches a raw `bash` with sshd's default environment
  (`HOME=/home/sandbox`, default PATH).
- The shell is spawned with `euid 0` (setuid nsenter) but with sandbox's env,
  which is neither "root env" nor "sandbox env" — a broken half-state.

The desired semantics: **the session should behave as if the user opened a root
shell inside the env container using `docker exec -it <env> /bin/bash`, with
the environment that the image/docker run configured.**

## Design decisions (agreed)

1. **Access model:** the shell runs as **root** with the env container's real
   environment. No sudo required; `apt-get`/`pip`/installs work directly.
2. **Landing directory:** `/workspace` (the project dir). Fall back to `/` if
   it does not exist.
3. **Scope:** fix at **deploy time** (access container runtime + worker deploy
   prep) **and** bake equivalent guarantees into the builder's generated env
   image, so both existing and newly built sessions behave identically.

## Key idea

The access container shares the **PID namespace** with the env container, so
`/proc/1/environ` inside the access container **is the env container's own
environment** (the `docker run` of `sleep infinity`). Read it at login time to
reconstruct the env container's `PATH`/`HOME`/`CONDA_PREFIX`/etc., then have
nsenter exec `bash -l` in `/workspace` with that environment. This is generic —
works for `pytorch/pytorch`, `python:*-slim`, derived training images, and
future base images with zero per-image hardcoding.

## Implementation

### 1. `AccessContainer/access-entrypoint.sh` (source of truth, deployed image)

Replace the static `ForceCommand` with a runtime wrapper script that
reconstructs the environment before entering the namespaces.

- Add a script (copied + chmod +x in Dockerfile) e.g. `/usr/local/bin/enter-env.sh`:
  - Parse `/proc/1/environ` (NUL-delimited) inside the access container.
  - Drop hostile vars: `PWD`, `SHLVL`, `_`, `OLDPWD`, `SSH_*`.
  - Keep everything valuable: `PATH`, `HOME`, `LANG`, `TERM` (if set),
    `CONDA_PREFIX`, `CONDA_DEFAULT_ENV`, `PYTHON*`, `VIRTUAL_ENV`, `TZ`, etc.
  - Sanitize/backfill:
    - `HOME`: prefer `HOME` from `/proc/1/environ`; fall back to `/root`.
    - `PATH`: fall back to a canonical list
      (`/opt/conda/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin`)
      if missing/empty.
  - `exec nsenter -t 1 -m -u -i -n -p -- <bash> -c '<exports>; cd /workspace 2>/dev/null || cd /; exec /bin/bash -l'`
  - Important: pass the reconstructed environment **explicitly** (via
    `bash -c 'export ...; exec ...'` or by env-var injection), not by relying
    on nsenter's `--preserve-environment` (which would preserve the *access*
    container's env — wrong).
- `ForceCommand /usr/local/bin/enter-env.sh` in the `sandbox.conf`
  `sshd_config.d` snippet.
- Keep the setuid-runtime re-assert (`chown root:root /usr/bin/nsenter;
  chmod u+s /usr/bin/nsenter`) — still required for the `sandbox` SSH login to
  enter the namespaces as root.
- Do NOT change the sshd login user: stays `sandbox`. Only the forced shell
  runs as root.

### 2. `AccessContainer/Dockerfile`

- `COPY enter-env.sh /usr/local/bin/enter-env.sh` + `chmod +x`.
- Ensure `util-linux` (nsenter) present (already there) and `bash` present
  (already there).
- Operator note: image is rebuilt/pushed once (`aorko123/access-sshd:latest`);
  existing workers pull the new tag per-session, so no worker redeploy is
  needed — but the image **must** be pushed for the fix to take effect.

### 3. `Worker/interactive_handler.py` — deploy-time prep for existing/derived images

Derived sessions reuse the training image which may not have `/workspace` as the
project dir or a root login profile. Extend `_prepare_env_container`
(best-effort, non-fatal, all logs swallowed as today):

- Ensure `/workspace` exists (fallback `/output` mount target convention).
- Ensure a minimal `/root/.bash_profile` and `/root/.bashrc` so `bash -l` with
  `HOME=/root` sources conda/virtualenv/PATH setup from the base image
  (`[ -f "$HOME/.bashrc" ] && . "$HOME/.bashrc"`), mirroring what
  `generate_env_dockerfile` bakes for direct sessions.
- Create `/home/sandbox` for the sshd auth user (already implemented; keep it
  only for the login user's `authorized_keys`), no longer required for the
  shell environment.
- Do NOT change the container run flags (still `--pid container:<env>`,
  `--userns=host`, `--cap-add ALL`).

### 4. `Docker_Image_Builder/docker_ops.py` — bake guarantees for future interactive env images

Keep `generate_env_dockerfile` and `generate_access_entrypoint` consistent with
the runtime fix so newly built images converge immediately:

- `generate_env_dockerfile`:
  - Keep `WORKDIR /workspace`, `/home/sandbox`, tool install, `sleep infinity`.
  - Ensure `/root/.bash_profile` sources `/root/.bashrc` (already partially
    done — make it unconditional, not gated on `[ ! -f ... ]`).
  - (Optional) install `sudo` is NOT needed (root shell model) — document that
    in the Dockerfile comment.
- `generate_access_entrypoint` (duplicated generator used for reference/tests):
  - Mirror the new `enter-env.sh` ForceCommand from step 1 so the generator and
    the checked-in image stay in sync.
- note in docker_ops docstrings that `aorko123/access-sshd:latest` is rebuilt
  from `AccessContainer/`.

### 5. Gateway — no change

`gateway/session_client.py` connects as `sandbox` (`SSH_USER`); sshd still
authenticates `sandbox`. Only the forced command escalates to root. No gateway
code change.

## Acceptance criteria

1. `ssh sandbox@<gateway> -p <port>` with the ephemeral password lands in
   `/workspace` (or `/`) as `root`.
2. `echo $PATH` includes the env image's python/conda dir (e.g. `/opt/conda/bin`
   for pytorch images); `python --version` and `pip list` show the version and
   `requirements.txt` packages the user submitted.
3. `apt-get update && apt-get install <pkg>` succeeds as root without sudo.
4. `pip install <pkg>` works; new packages persist in the env container for the
   session (host changes written to the env container's fs.
5. Editing files under `/workspace` persists; changes are visible across
   reconnects.
6. VS Code Remote-SSH connects, installs the server into `$HOME`, and the
   integrated terminal runs in `/workspace` with the same env.
7. Env container exit / session timeout still torn down as before
   (unchanged monitor/stop logic).

## Verification procedure

- Manual (primary): run through acceptance 1-6 on a direct interactive session
  (base `pytorch` image) and a derived one (base training job image). Confirm
  PATH,HOME via `docker exec <env> printenv | grep -E '^(PATH|HOME)`.
- Check that the access container's `/proc/1/environ` == env container's env
  (`docker exec <access> tr '\0' '\n' < /proc/1/environ`).
- Regression: existing unit tests still pass:
  `cd Docker_Image_Builder && pytest` (test_docker_ops, test_builder,
  test_database). Add a test asserting `generate_env_dockerfile`/entrypoint
  contain the new ForceCommand / `/root/.bash_profile` sourcing.
- Rebuild + push the access image once and re-test an in-flight session after
  the operator rebuild.

## Edge cases / risks

- **Alpine/other-base env images:** `bash` may not exist in the env container;
  `/bin/sh` fallback in the nsenter command. `/proc/1/environ` parsing is
  generic.
- **Non-writable `/workspace`:** as root this is always writable; if the image
  has `/` as WORKDIR, landing in `/` (already handled).
- **Very large env (/proc/1/environ):** cap exports at ~64KB of keys to avoid
  execve E2BIG argument blowup; log/ignore overflow.
- **`TERM` for rich TTY:** carry `TERM` from the sshd session (access side) over
  the env exports (T might be needed for colors / vscode). Decide: keep sshd's
  `TERM`, prefer env container's if set.
- **Multi-user sessions:** session is single-owner (one SSH user) — root shell
  barely affects this, but document in the repo that interactive sessions are
  root-on-env-container by design.
- **docker_ops generator drift:** the checked-in `AccessContainer/` image and
  `generate_access_entrypoint()` must not drift; keep a comment pointing each at
  the other.