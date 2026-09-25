"""SSH-capable image profile: additive, old digests never relabelled."""
import interactive_build as build


def _item():
    return {"workspace_id": "11111111-1111-1111-1111-111111111111",
            "id": "22222222-2222-2222-2222-222222222222",
            "attempt_id": "33333333-3333-3333-3333-333333333333",
            "origin": "UPLOAD", "source_job_id": ""}


def test_dockerfile_has_ssh_prereqs_and_label():
    text = build.dockerfile(_item(), "docker.io/pytorch/pytorch@sha256:" + "a" * 64, True)
    for needle in ("openssh-server", "tar", "curl", "bash", "ca-certificates",
                   "io.dml.vscode-ssh-profile", "dml-ssh-session"):
        assert needle in text
    # Existing developer profile preserved.
    assert "io.dml.developer-profile" in text
    assert "USER 10001:10001" in text
    # Locked dml account denies pubkey even with the right key installed;
    # `*` keeps password login impossible while allowing pubkey.
    assert "usermod" in text and "'*'" in text


def test_ssh_profile_detection():
    assert build.ssh_profile_of_attrs({"Config": {"Labels": {"io.dml.vscode-ssh-profile": "v1"}}}) == "v1"
    assert build.ssh_profile_of_attrs({"Config": {"Labels": {}}}) is None
    assert build.ssh_profile_of_attrs({}) is None


def test_session_wrapper_lands_in_workspace():
    text = build.ssh_session_wrapper_bytes().decode()
    assert "cd /workspace" in text
    assert "HOME=/home/dml" in text
    assert "/opt/dml-venv" in text
    assert "SSH_ORIGINAL_COMMAND" in text
