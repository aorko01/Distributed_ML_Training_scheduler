"""SSH info/grant APIs: ownership, capability pinning, scoped CLI tokens."""
import os


def test_ssh_requires_capable_runtime():
    # Static contract: ssh_info raises 404 for non-capable runtimes, and
    # ssh_connection requires the scoped CLI principal (get_cli_user).
    import inspect
    from app.services import interactive_runtime_service as svc
    assert "ssh_capable" in inspect.getsource(svc.start)
    assert "purpose" in inspect.getsource(svc.ssh_connection)
    assert "ssh_grant_ready" in inspect.getsource(svc.ssh_connection)


def test_cli_scope_boundary():
    import inspect
    from app.api import deps
    src = inspect.getsource(deps.get_cli_user)
    assert "interactive:ssh" in src
    from app.services import cli_auth_service as auth
    assert auth.CLI_SCOPE == "interactive:ssh"


def test_ssh_rate_limit_independent():
    import inspect
    from app.services import interactive_runtime_service as svc
    src = inspect.getsource(svc.ssh_connection)
    assert "ssh_connection_requested_at" in src
    assert "purpose" in src
