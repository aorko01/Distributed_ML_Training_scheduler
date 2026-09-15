"""Real Docker Hub end-to-end: login-failure -> relogin -> retry.

Covers the auth-error-triggered relogin-and-retry loop in ``docker_ops.py``
against a real Docker Hub account (no mocks for login/build/push):

- ``test_push_auth_failure_triggers_relogin_and_retry``: real login, real
  build of a throwaway image via the service's own ``build_push_and_clean``
  code path, a simulated *trigger only* (first ``push`` call yields an
  auth-rejection chunk), then the production code's own detection,
  real re-authentication, real second push, and a real Hub-API check that
  the tag landed. Local image + Hub repo are always cleaned up.
- ``test_login_with_wrong_credentials_reports_failure``: fully real negative
  case — real ``docker_login`` with bad credentials must return ``False``.

Gating (see ``test/e2e/conftest.py`` + ``pytest.ini``):

- Marked ``real_dockerhub``; skipped unless ``--run-real-dockerhub`` or
  ``RUN_REAL_DOCKERHUB_E2E=1`` is set, so the default suite never spends
  Hub rate-limit budget.
- Skips (does not fail) when ``DOCKER_HUB_USERNAME`` / ``DOCKER_HUB_PASSWORD``
  are absent or still placeholders.

Interception point (deliberately minimal, production logic untouched):

- Only ``client.images.push`` is wrapped: the first call returns
  ``iter([{"error": "unauthorized: authentication required"}])`` to simulate
  the registry rejecting one push. Every downstream step — ``_is_auth_error``
  detection, ``ensure_logged_in`` / ``docker_login`` (real ``client.login``),
  the second ``push`` (delegated to the original bound method), and the
  Hub-API verification — runs for real. ``client.login`` is wrapped with a
  counting passthrough purely to *observe* the real relogin, never to fake it.

Assumes a disposable/test Hub account: every run pushes a uniquely named
repository ``<username>/e2e-retry-<hex>:latest`` and deletes it afterwards.
"""

import os
import time
import uuid
import warnings

import pytest

pytestmark = pytest.mark.real_dockerhub

import docker
import docker.errors
import requests

import database
import docker_ops

HUB_LOGIN_URL = "https://hub.docker.com/v2/users/login/"
HUB_API_BASE = "https://hub.docker.com/v2/repositories"

# Small, fast base so the real build stays cheap. No requirements.txt is
# created on purpose, so generate_dockerfile() skips `pip install`.
E2E_BASE_IMAGE = "alpine:3.19"
E2E_COMMAND = "echo e2e-ok"
E2E_TAG = "latest"

_DUMMY_USERNAMES = {"", "testuser", "ci-test-user", "your-dockerhub-username"}
_DUMMY_PASSWORDS = {"", "your-dockerhub-password-or-access-token", "changeme"}


def _get_real_credentials():
    """Return (username, password) or (None, None) when unavailable/placeholder."""
    username = (os.environ.get("DOCKER_HUB_USERNAME") or "").strip()
    password = (os.environ.get("DOCKER_HUB_PASSWORD") or "").strip()
    if not username or username.lower() in _DUMMY_USERNAMES:
        return None, None
    if username.lower().startswith("your-") or username.startswith("<"):
        return None, None
    if not password or password.lower() in _DUMMY_PASSWORDS:
        return None, None
    if password.lower().startswith("your-") or password.startswith("<"):
        return None, None
    return username, password


def _require_real_credentials():
    creds = _get_real_credentials()
    if creds == (None, None):
        pytest.skip(
            "real Docker Hub e2e needs DOCKER_HUB_USERNAME and "
            "DOCKER_HUB_PASSWORD in the environment (or .env_test); skipping"
        )
    return creds


def _require_daemon(client: docker.DockerClient):
    """Fail loudly (not silently skip) when the daemon is unreachable."""
    try:
        client.ping()
    except Exception as exc:
        pytest.fail(
            "Docker daemon not reachable (docker.from_env().ping() failed: "
            f"{exc}). The real-Hub e2e needs a local Docker daemon.",
            pytrace=False,
        )


def _docker_client_or_fail() -> docker.DockerClient:
    """Create a real client, failing loudly when the daemon is unreachable.

    ``docker.from_env()`` itself performs a version negotiation, so a missing
    daemon raises here — convert that into a clear failure instead of a
    confusing traceback.
    """
    try:
        client = docker.from_env()
    except Exception as exc:
        pytest.fail(
            "Docker daemon not reachable (docker.from_env() failed: "
            f"{exc}). The real-Hub e2e needs a local Docker daemon.",
            pytrace=False,
        )
    _require_daemon(client)
    return client


def _unique_job_id(prefix: str = "e2e-retry") -> str:
    # Lowercase hex only: valid as a Docker Hub repository name and unique
    # across repeated/parallel runs.
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Docker Hub HTTP API helpers (login / tag check / repo delete)
# ---------------------------------------------------------------------------

def _hub_token(username: str, password: str) -> str:
    resp = requests.post(
        HUB_LOGIN_URL,
        json={"username": username, "password": password},
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json().get("token")
    if not token:
        raise AssertionError("Hub login response had no token")
    return token


def _hub_tag_exists(namespace: str, repo: str, tag: str, token: str) -> bool:
    url = f"{HUB_API_BASE}/{namespace}/{repo}/tags/{tag}"
    resp = requests.get(
        url, headers={"Authorization": f"JWT {token}"}, timeout=30
    )
    if resp.status_code == 200:
        return True
    if resp.status_code == 404:
        return False
    resp.raise_for_status()
    return False  # pragma: no cover


def _wait_for_hub_tag(namespace: str, repo: str, tag: str, token: str,
                       timeout: float = 180.0, interval: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if _hub_tag_exists(namespace, repo, tag, token):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def _hub_delete_repo(namespace: str, repo: str, token: str) -> None:
    url = f"{HUB_API_BASE}/{namespace}/{repo}/"
    resp = requests.delete(
        url, headers={"Authorization": f"JWT {token}"}, timeout=30
    )
    if resp.status_code in (202, 204, 404):
        return
    resp.raise_for_status()


def _remove_local_image(client: docker.DockerClient, ref: str) -> None:
    try:
        client.images.remove(image=ref, force=True)
    except docker.errors.ImageNotFound:
        pass
    except Exception as exc:  # best-effort cleanup: warn, don't mask test result
        warnings.warn(f"local cleanup of {ref} failed: {exc}")


def _delete_hub_repo_best_effort(namespace: str, repo: str, password: str) -> None:
    try:
        token = _hub_token(namespace, password)
    except Exception as exc:
        warnings.warn(f"Hub cleanup login failed, cannot delete {namespace}/{repo}: {exc}")
        return
    try:
        _hub_delete_repo(namespace, repo, token)
    except Exception as exc:
        warnings.warn(f"Hub cleanup delete of {namespace}/{repo} failed: {exc}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.real_dockerhub
def test_login_with_wrong_credentials_reports_failure(monkeypatch):
    """Fully real negative case: bad credentials -> docker_login() is False."""
    username, _password = _require_real_credentials()
    client = _docker_client_or_fail()

    old_logged_in = docker_ops._logged_in
    docker_ops._logged_in = False
    try:
        monkeypatch.setattr(docker_ops, "DOCKER_HUB_USERNAME", username)
        monkeypatch.setattr(
            docker_ops, "DOCKER_HUB_PASSWORD", f"e2e-wrong-{uuid.uuid4().hex[:8]}"
        )
        assert docker_ops.docker_login(client) is False
        assert docker_ops._logged_in is False
    finally:
        docker_ops._logged_in = old_logged_in


@pytest.mark.real_dockerhub
def test_push_auth_failure_triggers_relogin_and_retry(monkeypatch, tmp_path):
    """Real login -> real build -> fake first-push auth error -> real retry.

    Only the trigger is simulated (first ``images.push`` yields one
    auth-error chunk). The retry loop, relogin, second push, and Hub-API
    verification are all real production code paths.
    """
    username, password = _require_real_credentials()
    client = _docker_client_or_fail()

    # Isolate the base-image LRU DB so the e2e never touches /data/builder.db.
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "e2e-builder.db"))
    database.init_db()

    # build_push_and_clean() reads these module globals for the tag + login,
    # which were frozen at import time to dummy values — point them at the
    # real disposable account for this test only.
    monkeypatch.setattr(docker_ops, "DOCKER_HUB_USERNAME", username)
    monkeypatch.setattr(docker_ops, "DOCKER_HUB_PASSWORD", password)

    # Throwaway project dir (never a real job's contents): one tiny file, no
    # requirements.txt so the build skips pip install and stays fast.
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "app.py").write_text('print("e2e-retry-ok")\n')
    (project_dir / "hello.txt").write_text("e2e\n")

    job_id = _unique_job_id()
    image_ref = f"{username}/{job_id}:{E2E_TAG}"

    old_logged_in = docker_ops._logged_in
    docker_ops._logged_in = False
    try:
        # -- 1. Real login -------------------------------------------------
        assert docker_ops.docker_login(client) is True

        # Observe (not fake) the relogin: counting passthrough to the real
        # client.login.
        login_calls: list = []
        original_login = client.login

        def _counting_login(*args, **kwargs):
            login_calls.append((args, kwargs))
            return original_login(*args, **kwargs)

        monkeypatch.setattr(client, "login", _counting_login)
        logins_before_push = len(login_calls)

        # Simulate ONLY the trigger: first push looks auth-rejected, every
        # later push delegates to the real bound method.
        push_calls: list = []
        original_push = client.images.push

        def _flaky_push(*args, **kwargs):
            push_calls.append((args, kwargs))
            if len(push_calls) == 1:
                return iter([{"error": "unauthorized: authentication required"}])
            return original_push(*args, **kwargs)

        monkeypatch.setattr(client.images, "push", _flaky_push)

        # -- 2. Real build + retry loop ------------------------------------
        result = docker_ops.build_push_and_clean(
            client, job_id, str(project_dir), E2E_COMMAND, E2E_BASE_IMAGE
        )

        assert result is None, f"expected success after retry, got: {result!r}"
        assert len(push_calls) == 2, (
            f"expected exactly 2 push attempts (fake auth error + real retry), "
            f"got {len(push_calls)}"
        )
        assert len(login_calls) > logins_before_push, (
            "expected the retry loop to perform a real re-login after the "
            "auth error, but client.login was not called again"
        )

        # -- 3. Real Hub-API check that the image actually landed ----------
        token = _hub_token(username, password)
        assert _wait_for_hub_tag(username, job_id, E2E_TAG, token), (
            f"pushed tag {username}/{job_id}:{E2E_TAG} not visible via "
            "Docker Hub API within timeout"
        )
    finally:
        # -- 4. Always clean up -------------------------------------------
        _remove_local_image(client, image_ref)
        _delete_hub_repo_best_effort(username, job_id, password)
        docker_ops._logged_in = old_logged_in
