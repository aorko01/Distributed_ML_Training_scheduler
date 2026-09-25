"""Grant purpose: browser default, SSH bounded deadline, old tickets valid."""
import jwt
from headscale_management.schemas import Issue
from .conftest import headers, ready


def test_issue_defaults_browser():
    body = Issue(user="u1", resource_id="r1", service="workspace", gateway_id="gateway-main",
                 generation="1", authorized=True)
    assert body.purpose == "browser"


def test_old_ticket_without_purpose_defaults_browser(system):
    service = system.app.state.grants
    old = {"iss": service.settings.issuer, "aud": service.settings.gateway_id, "sub": "u",
           "jti": "j", "iat": int(service.clock()), "nbf": int(service.clock()),
           "exp": int(service.clock()) + 60, "resource_id": "r", "generation": "1",
           "service": "workspace", "protocol": "tcp-stream-v1"}
    ticket = jwt.encode(old, service.private, algorithm="EdDSA",
                        headers={"kid": service.settings.signing_kid})
    claims = service.decode(ticket, admission=True)
    assert claims["purpose"] == "browser"


def test_ssh_grant_uses_extended_deadline(system):
    ready(system, resource="ssh-r", owner="user-a")
    browser = system.client.post("/internal/v1/access-grants",
        json={"user": "user-a", "resource_id": "ssh-r", "generation": "g1",
              "service": "echo", "gateway_id": "gateway-main", "authorized": True},
        headers=headers(system)).json()
    ssh = system.client.post("/internal/v1/access-grants",
        json={"user": "user-a", "resource_id": "ssh-r", "generation": "g1",
              "service": "echo", "gateway_id": "gateway-main", "authorized": True,
              "purpose": "ssh"},
        headers=headers(system)).json()
    assert browser["grant_id"] != ssh["grant_id"]
    grants = system.app.state.grants
    assert grants.settings.ssh_session_max >= grants.settings.session_max
    ssh_claims = grants.decode(ssh["ticket"], admission=True)
    assert ssh_claims["purpose"] == "ssh"
