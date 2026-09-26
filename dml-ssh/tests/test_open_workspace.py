import argparse
from types import SimpleNamespace

import pytest

from dml_ssh import cli


def test_configure_opens_the_remote_workspace(monkeypatch, tmp_path):
    runtime = "9f3a1c2d3e4f5a6b7c8d9e0f1a2b3c4d"
    info = {
        "runtime_id": runtime,
        "generation": 4,
        "ssh_capable": True,
        "ssh_ready": True,
        "host_key": "ssh-ed25519 test",
    }
    monkeypatch.setattr(cli._client, "ensure_access", lambda base: "token")
    monkeypatch.setattr(cli._client, "ssh_info", lambda base, token, ident: info)
    monkeypatch.setattr(cli._sshconfig, "ensure_key", lambda path: tmp_path / "id.pub")
    (tmp_path / "id.pub").write_text("ssh-ed25519 client\n")
    monkeypatch.setattr(cli._sshconfig, "write_snippet", lambda *args, **kwargs: 0)
    monkeypatch.setattr(cli._sshconfig, "write_known_host", lambda *args, **kwargs: 0)
    monkeypatch.setattr(cli._sshconfig, "prune_old_keys", lambda *args: [])
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/code")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == ["ssh", "-G"]:
            return SimpleNamespace(returncode=0, stdout="proxycommand /usr/bin/dml-ssh proxy --runtime id\n")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli.subprocess, "run", run)
    args = argparse.Namespace(scheduler="https://scheduler.example", runtime=runtime,
                              identity="", output=str(tmp_path / "dml-config"),
                              keep_previous=False, open=True)
    cli.cmd_configure(args)
    alias = f"dml-{runtime}-g4"
    assert calls == [
        ["ssh", "-G", alias],
        ["/usr/bin/code", "--folder-uri", f"vscode-remote://ssh-remote+{alias}/workspace"],
    ]


def test_configure_does_not_open_without_active_ssh_host(monkeypatch, tmp_path):
    runtime = "9f3a1c2d3e4f5a6b7c8d9e0f1a2b3c4d"
    info = {"runtime_id": runtime, "generation": 1, "ssh_capable": True,
            "ssh_ready": True, "host_key": "ssh-ed25519 test"}
    monkeypatch.setattr(cli._client, "ensure_access", lambda base: "token")
    monkeypatch.setattr(cli._client, "ssh_info", lambda base, token, ident: info)
    monkeypatch.setattr(cli._sshconfig, "ensure_key", lambda path: tmp_path / "id.pub")
    (tmp_path / "id.pub").write_text("ssh-ed25519 client\n")
    monkeypatch.setattr(cli._sshconfig, "write_snippet", lambda *args, **kwargs: 0)
    monkeypatch.setattr(cli._sshconfig, "write_known_host", lambda *args, **kwargs: 0)
    monkeypatch.setattr(cli._sshconfig, "prune_old_keys", lambda *args: [])
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/code")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="proxycommand none\n")

    monkeypatch.setattr(cli.subprocess, "run", run)
    args = argparse.Namespace(scheduler="https://scheduler.example", runtime=runtime,
                              identity="", output=str(tmp_path / "dml-config"),
                              keep_previous=False, open=True)
    with pytest.raises(SystemExit):
        cli.cmd_configure(args)
    assert len(calls) == 1 and calls[0][:2] == ["ssh", "-G"]
