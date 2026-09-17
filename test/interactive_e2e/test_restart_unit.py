"""Portable command-contract tests. No Docker, credentials or live services."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def deployment(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    shutil.copyfile(ROOT / "restart.sh", checkout / "restart.sh")
    (checkout / "Scheduler").mkdir()
    (checkout / "Scheduler/docker-compose.yml").write_text("services: {}")
    (checkout / "deploy/interactive").mkdir(parents=True)
    (checkout / "deploy/interactive/compose.yaml").write_text("services: {}")
    envfile = tmp_path / "host.env"
    envfile.write_text("no-secrets-in-test=1")
    binary = tmp_path / "bin"
    binary.mkdir()
    log = tmp_path / "commands.jsonl"
    docker = binary / "docker"
    docker.write_text('''#!/usr/bin/env python3
import json, os, sys, time
args=sys.argv[1:]
with open(os.environ["FAKE_LOG"],"a") as f: f.write(json.dumps(args)+"\\n")
text=" ".join(args)
if os.environ.get("FAKE_FAIL") and os.environ["FAKE_FAIL"] in text: sys.exit(7)
if "config --format json" in text: print(json.dumps({"volumes":{"postgres-data":{"name":"scheduler-postgres-data"}}}))
elif "ps --quiet db" in text: print("database-id")
elif args and args[0]=="inspect": print("scheduler-postgres-data")
elif "port api 8000" in text: print("0.0.0.0:8000")
if "build api" in text: time.sleep(float(os.environ.get("FAKE_BUILD_DELAY","0")))
''')
    curl = binary / "curl"
    curl.write_text("#!/usr/bin/env sh\nexit 0\n")
    for path in (docker, curl):
        path.chmod(0o755)
    env = {**os.environ, "PATH": str(binary) + ":" + os.environ["PATH"], "FAKE_LOG": str(log),
        "INTERACTIVE_ENV_FILE": str(envfile), "REQUIRE_INTERACTIVE": "1", "RESTART_WAIT_TIMEOUT": "5"}
    return checkout, log, env


def execute(deployment):
    checkout, log, env = deployment
    result = subprocess.run(["bash", str(checkout / "restart.sh")], env=env, capture_output=True, text=True, timeout=10)
    commands = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return result, commands


@pytest.mark.parametrize("failure", ["config --quiet", "build api", "build management gateway"])
def test_preflight_and_build_failure_never_stop_or_recreate(deployment, failure):
    deployment[2]["FAKE_FAIL"] = failure
    result, commands = execute(deployment)
    assert result.returncode != 0
    assert not any("stop" in cmd or "up" in cmd for cmd in commands)


@pytest.mark.parametrize("failure", ["run --rm --no-deps migrate", "run --rm --no-deps bootstrap", "--force-recreate --wait --wait-timeout 5 gateway",
    "--force-recreate --wait --wait-timeout 5 management"])
def test_migration_bootstrap_and_health_failures_remain_failures(deployment, failure):
    deployment[2]["FAKE_FAIL"] = failure
    result, _ = execute(deployment)
    assert result.returncode == 7 and "partially updated" in result.stderr


def test_absent_enabled_manifest_and_env_fail_before_build(deployment):
    checkout, _, env = deployment
    (checkout / "deploy/interactive/compose.yaml").unlink()
    result, commands = execute(deployment)
    assert result.returncode != 0 and not any("build" in cmd for cmd in commands)


def test_safe_sequence_and_bounded_waits(deployment):
    result, commands = execute(deployment)
    assert result.returncode == 0
    texts = [" ".join(cmd) for cmd in commands]
    assert next(i for i, t in enumerate(texts) if "build management gateway" in t) < next(i for i, t in enumerate(texts) if "stop --timeout" in t)
    assert any("--no-recreate" in cmd and "db" in cmd for cmd in commands)
    for cmd in commands:
        assert not any(token in cmd for token in ("down", "prune", "rmi", "restart"))
        if "up" in cmd:
            assert "--wait-timeout" in cmd
    assert not any("--force-recreate" in cmd and "tailscale" in cmd for cmd in commands)


def test_lock_serializes_concurrent_invocations(deployment):
    checkout, log, env = deployment
    env["FAKE_BUILD_DELAY"] = "0.3"
    first = subprocess.Popen(["bash", str(checkout / "restart.sh")], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    second = subprocess.Popen(["bash", str(checkout / "restart.sh")], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    first.communicate(timeout=10)
    second.communicate(timeout=10)
    assert first.returncode == second.returncode == 0
    commands = [json.loads(line) for line in log.read_text().splitlines()]
    docker_info = [i for i, cmd in enumerate(commands) if cmd == ["info"]]
    last_first_ps = next(i for i, cmd in enumerate(commands) if cmd[-1] == "ps" and "scheduler" in cmd)
    assert len(docker_info) == 2 and docker_info[1] > last_first_ps
