# VS Code Remote-SSH — client machine runbook

Follow this on the **client machine** (your laptop/desktop: Linux, macOS, or
Windows). It covers the one-time setup, then the per-runtime copy-paste flow
from the web UI. Native VS Code Remote-SSH into your workload container as
`dml`, working directory `/workspace` — same live files, Python environment,
and GPU as the browser editor.

## 0. What you need

- VS Code with the **Remote-SSH** extension installed.
- An **OpenSSH client**: `ssh -V` must print a version.
- **Python 3.9+** with `pip`.
- A checkout (or copy) of this repo — only for `pip install ./dml-ssh`.
- Your normal platform username/password (for `dml-ssh login`).
- A workspace whose page shows the **"Connect with VS Code"** block (means a
  runtime is `READY` and SSH-capable). If the page instead says the runtime
  is not SSH-capable, stop here: rebuild the image / start a new runtime
  first — no client-side step can work around that.

## 1. One-time setup (do once per client machine)

```bash
# 1a. Install the helper CLI (needs only requests + websocket-client).
pip install ./dml-ssh

# 1b. Sanity check: ssh client present, credential state shown.
dml-ssh doctor

# 1c. Log in (one password prompt; browser JWT is never used here).
dml-ssh login --scheduler https://scheduler.zulfiker.xyz
# expect: "logged in (interactive:ssh scope)"
```

Optional: export the scheduler URL so later commands are shorter:

```bash
export DML_SCHEDULER_URL=https://scheduler.zulfiker.xyz
# (add to ~/.bashrc / ~/.zshrc to persist)
```

## 2. Per runtime: copy-paste from the web UI (do for every new runtime)

1. Open your workspace page in the browser. When the runtime is `READY` and
   SSH-capable, the **"Connect with VS Code"** block shows a command with
   your real runtime ID and scheduler host already filled in. Copy it —
   it looks like this (IDs differ per runtime):

   ```bash
   dml-ssh configure 9f3a1c2d3e4f5a6b7c8d9e0f1a2b3c4d --scheduler https://scheduler.zulfiker.xyz
   ```

2. Paste and run it in your terminal. First run creates `~/.ssh/dml-<id>`
   (Ed25519) if missing, pins the workload host key, and writes
   `~/.ssh/dml-config`. If it prints an `Include` instruction, run it once:

   ```bash
   echo "Include ~/.ssh/dml-config" >> ~/.ssh/config
   ```

   Success prints the host name, e.g. `configured host dml-9f3a…-g4`.

3. In VS Code: **F1 → "Remote-SSH: Connect to Host"** → pick the printed
   host (`dml-<runtime-id>-g<generation>`) → **File → Open Folder →
   `/workspace`**.

4. Verify in a VS Code terminal (it runs inside your container):

   ```bash
   pwd    # must print /workspace
   id -u  # must print 10001
   python -c "import torch; print(torch.cuda.is_available())"
   ```

Done. Terminals, debugger, and extensions now run in the container; file
edits are instantly visible in the browser editor and vice versa.

## 3. Everyday use

- **Reconnect**: just connect again — each SSH connection fetches a fresh
  single-use grant invisibly via the `ProxyCommand`. Nothing to refresh.
- **Dev servers/notebooks**: VS Code **Ports view → Forward a Port** to reach
  workload loopback services (e.g. `127.0.0.1:8888`). No firewall/Docker
  changes needed.
- **Copy files**: `scp model.pt dml-<id>-g<n>:/workspace/` (same host name;
  SFTP clients work too).
- **New runtime?** Repeat section 2 only (one command + connect). Old host
  names stop working by design once their runtime is stopped or replaced.

## 4. Troubleshooting

| Symptom | Meaning / fix |
|---|---|
| `configure` says runtime not SSH-capable | Started from an older image. Rebuild the image, start a new runtime. |
| `configure` says SSH not ready / still starting | Wait for `READY`, retry the same command. |
| `dml-ssh proxy: …` / "run dml-ssh login first" | Token expired or revoked. `dml-ssh login --scheduler https://scheduler.zulfiker.xyz`, then retry. |
| VS Code reports host-key mismatch | You are dialling an old generation. Re-run the current `dml-ssh configure` from the page; never bypass with `StrictHostKeyChecking=no`. |
| Connection drops | Reconnect. If the runtime was Stopped (or hit the 4h session cap), that is expected — start/connect anew. |
| `dml-ssh doctor` shows ssh MISSING | Install an OpenSSH client for your OS first. |

## 5. Session end and safety notes

- **Stop** in the web UI ends SSH immediately — VS Code disconnects on
  purpose. Runtime files follow the usual temporary-runtime rule (unsaved
  container changes are discarded; the saved image stays).
- Never paste your browser's local-storage JWT into SSH configs or commands;
  `dml-ssh login` is the only supported credential path.
- `dml-ssh proxy` output on stdout is binary SSH data only; diagnostics go to
  stderr. Tickets never appear in URLs, shell history-safe files, or logs —
  don't add them to your own scripts either.
- `dml-ssh logout` revokes the stored refresh token on that machine.
