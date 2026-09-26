# dml-ssh — local CLI for VS Code Remote-SSH (plan.md §5)

Native VS Code Remote-SSH into the workload container (`dml`, `/workspace`).
The user never SSHes into the Worker host, Access container, or Gateway.

## Install

```bash
pip install ./dml-ssh
# requires python>=3.9, OpenSSH client installed
```

## Flow

```bash
dml-ssh login --scheduler https://scheduler.example.internal
dml-ssh configure <workspace-or-runtime-id> --scheduler https://scheduler.example.internal --open
# Opens the remote /workspace folder in VS Code. If prompted, add the printed
# Include line to ~/.ssh/config and run configure again. Without --open, run
# the printed `code --folder-uri .../workspace` command after configure.
dml-ssh doctor
dml-ssh logout
```

`configure` resolves the owned READY runtime, confirms `ssh_capable` and
generation, creates `~/.ssh/dml-<id>` Ed25519 identity if needed, fetches the
pinned workload host key via authenticated `GET /interactive/runtimes/{id}/ssh-info`,
and writes a generated SSH config snippet (separate file + `Include`
instruction; existing `~/.ssh/config` touched only with consent + backup).

Each `ProxyCommand` (`dml-ssh proxy ...`) obtains a fresh single-use
SSH-purpose grant, opens WSS, authenticates within 5s, sends versioned
`SSH_OPEN` with the local public key + generation, waits for `SSH_READY`,
then copies stdin/stdout as binary bytes. Errors go to stderr, never stdout.
Tickets never appear in URLs/argv/env/disk/logs.
