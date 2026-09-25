import io
import os
from pathlib import Path
import stat
from unittest.mock import MagicMock, patch
import uuid
import zipfile
import pytest
import interactive_build as build
import interactive_api as api
import builder


def item(origin='UPLOAD'):
    return {'id': str(uuid.uuid4()), 'workspace_id': str(uuid.uuid4()), 'attempt_id': str(uuid.uuid4()),
            'origin': origin, 'base_image': 'pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime',
            'source_image_tag': 'user/source:build-a', 'source_job_id': str(uuid.uuid4()), 'source_object_key': 'private/key'}


def zip_data(files=None):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        for path, content in (files or {'project/requirements.txt': '', 'project/probe.py': 'x'}).items():
            archive.writestr(path, content)
    return data.getvalue()


def test_dockerfiles_and_tags():
    source = item()
    base = 'pytorch/pytorch@sha256:' + 'a' * 64
    output = build.dockerfile(source, base, True)
    assert 'COPY project/' in output and 'pip install' in output
    assert 'USER 10001:10001' in output
    assert 'CMD ' not in output
    # SSH tooling and its fixed session wrapper now belong in the workload;
    # tailnet/Docker agents and credentials still do not.
    assert all(word not in output.lower() for word in ('tailscale', 'docker.sock', 'password', 'token'))
    assert 'openssh-server' in output
    assert 'COPY --chown=0:0 dml-ssh-session /usr/local/bin/dml-ssh-session' in output
    assert 'sudo' in output.lower()
    assert '/opt/dml-venv/bin/python -m pip install' in output
    assert 'PIP_BREAK_SYSTEM_PACKAGES' not in output
    assert 'io.dml.developer-profile' in output and '"v1"' in output
    assert 'VIRTUAL_ENV=/opt/dml-venv' in output and 'HOME=/home/dml' in output
    assert output.rstrip().endswith('USER 10001:10001')
    assert 'VOLUME' not in output
    derived = build.dockerfile(item('EXISTING_JOB'), base, False)
    assert derived.startswith('FROM ' + base)
    assert 'CMD' not in derived
    assert [line for line in derived.splitlines() if line.startswith('COPY ')] == [
        'COPY --chown=0:0 dml-ssh-session /usr/local/bin/dml-ssh-session'
    ]
    assert 'WORKDIR /workspace' in derived
    assert 'USER 10001:10001' in derived
    assert 'io.dml.developer-profile' in derived
    first = build.tag_for(source)
    source['attempt_id'] = str(uuid.uuid4())
    assert build.tag_for(source) != first
    source['attempt_id'] = '../bad'
    with pytest.raises(ValueError):
        build.tag_for(source)


@pytest.mark.parametrize('files', [{'../requirements.txt': ''}, {'/requirements.txt': ''}, {'file.py': ''}, {'requirements.txt': '', 'Dockerfile': 'FROM bad'}, {'requirements.txt': '', '.dockerignore': 'requirements.txt'}])
def test_archive_rejections(tmp_path, files):
    with pytest.raises(build.BuildFailure) as exc:
        build.extract(zip_data(files), tmp_path)
    assert exc.value.kind == 'user'


def test_archive_symlink_and_bomb_and_count(tmp_path, monkeypatch):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        link = zipfile.ZipInfo('requirements.txt'); link.create_system = 3; link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, '/etc/passwd')
    with pytest.raises(build.BuildFailure):
        build.extract(data.getvalue(), tmp_path)
    monkeypatch.setattr(build, 'MAX_EXPANDED', 1)
    with pytest.raises(build.BuildFailure):
        build.extract(zip_data({'requirements.txt': 'too big'}), tmp_path)
    monkeypatch.setattr(build, 'MAX_EXPANDED', 512 * 1024 * 1024)
    monkeypatch.setattr(build, 'MAX_FILES', 1)
    with pytest.raises(build.BuildFailure):
        build.extract(zip_data(), tmp_path)
    with pytest.raises(build.BuildFailure):
        build.extract(b'not a zip', tmp_path)


def test_archive_nested_project(tmp_path):
    path = build.extract(zip_data(), tmp_path)
    assert (path / 'probe.py').read_text() == 'x'


def test_existing_job_resolves_digest_and_pushes():
    source = item('EXISTING_JOB')
    client = MagicMock()
    client.images.get_registry_data.return_value.attrs = {'Descriptor': {'digest': 'sha256:' + 'b' * 64}}
    def cli(directory, tag, cancel, on_line):
        content = (Path(directory) / 'Dockerfile').read_text()
        assert content.startswith('FROM user/source@sha256:')
        assert [line for line in content.splitlines() if line.startswith('COPY ')] == [
            'COPY --chown=0:0 dml-ssh-session /usr/local/bin/dml-ssh-session'
        ]
        assert (Path(directory) / 'dml-ssh-session').read_bytes() == build.ssh_session_wrapper_bytes()
        assert 'USER 10001:10001' in content
        return 0, [], False
    with patch.object(build, 'run_command') as command, patch.object(api, 'log'), patch.object(build, '_run_cancellable_docker_build', side_effect=cli):
        result = build.build(client, source, lambda: False)
    assert command.call_args_list[0].args[0] == ['pull', 'user/source:build-a']
    assert command.call_args_list[-1].args[0] == ['push', build.tag_for(source)]
    assert result['image_digest_ref'].endswith('@sha256:' + 'b' * 64)
    assert result['developer_profile'] == 'v1'


def test_build_reports_developer_profile_and_snapshot_propagates_label():
    assert build.developer_profile_of_attrs({'Config': {'Labels': {'io.dml.developer-profile': 'v1'}}}) == 'v1'
    assert build.developer_profile_of_attrs({'Config': {'Labels': {}}}) is None
    assert build.developer_profile_of_attrs({}) is None


@pytest.mark.parametrize('kind', ['user', 'system', 'cancelled'])
def test_process_failure_release_and_cleanup(kind, temp_db):
    source = item()
    registry = builder.BuildLeaseRegistry(); registry.accept_heartbeat({})
    error = build.Cancelled() if kind == 'cancelled' else build.BuildFailure(kind)
    with patch.object(build, 'build', side_effect=error), patch.object(api, 'log'), patch.object(api, 'request') as request, patch.object(build, '_remove_local_image') as cleanup:
        build.process(MagicMock(), source, registry)
    if kind == 'cancelled':
        request.assert_not_called()
    else:
        assert request.call_args.args[0] == ('failure' if kind == 'user' else 'release')
    cleanup.assert_called_once()
    assert registry.heartbeat_payload() == []


def test_success_callback_before_cleanup_and_late_cancel(temp_db):
    source = item(); registry = builder.BuildLeaseRegistry(); registry.accept_heartbeat({})
    result = {'image_tag': build.tag_for(source), 'image_digest_ref': 'user/repo@sha256:' + 'a'*64, 'resolved_base_digest': 'pytorch/pytorch@sha256:' + 'b'*64}
    order = []
    with patch.object(build, 'build', return_value=result), patch.object(api, 'log'), patch.object(api, 'request', side_effect=lambda *args: order.append('callback')), patch.object(build, '_remove_local_image', side_effect=lambda *args: order.append('cleanup')):
        build.process(MagicMock(), source, registry)
    assert order == ['callback', 'cleanup']
    def cancelled(*args):
        registry.cancel_all()
        return result
    with patch.object(build, 'build', side_effect=cancelled), patch.object(api, 'request') as request, patch.object(build, '_remove_local_image'):
        build.process(MagicMock(), source, registry)
    request.assert_not_called()


def test_service_secret(tmp_path, monkeypatch):
    path = tmp_path / 'secret'; path.write_text('0123456789abcdefghijklmnopqrstuvwxyzABCDEF'); path.chmod(0o600)
    monkeypatch.setenv('INTERACTIVE_BUILDER_SECRET_FILE', str(path))
    assert api.headers()['Authorization'].startswith('Bearer ')
    path.chmod(0o644)
    with pytest.raises(ValueError):
        api.headers()


def test_upload_build_copies_project_and_digest_base(tmp_path):
    source = item()
    response = MagicMock()
    response.__enter__.return_value = response
    response.iter_content.return_value = [zip_data()]
    client = MagicMock()
    client.images.get_registry_data.return_value.attrs = {'Descriptor': {'digest': 'sha256:' + 'c' * 64}}
    def cli(directory, tag, cancel, on_line):
        root = Path(directory)
        assert (root / 'project' / 'probe.py').read_text() == 'x'
        assert (root / 'project' / 'requirements.txt').exists()
        assert not (root / 'project' / 'Dockerfile').exists()
        content = (root / 'Dockerfile').read_text()
        assert content.startswith('FROM pytorch/pytorch@sha256:')
        assert 'COPY project/' in content
        assert 'CMD ' not in content
        return 0, [], False
    with patch('requests.get', return_value=response), patch.object(build, 'run_command'), patch.object(api, 'log'), patch.object(build, '_run_cancellable_docker_build', side_effect=cli):
        result = build.build(client, source, lambda: False)
    assert result['resolved_base_digest'] == 'pytorch/pytorch@sha256:' + 'c' * 64


def test_both_queues_get_work_without_starvation(tmp_path):
    import threading
    stop = threading.Event()
    order = []
    first = {'id': 'batch', 'object_key': 'key', 'command': '', 'docker_base_image': 'base:1', 'image_build_attempt_id': 'batch-attempt'}
    interactive = item()
    def batch_claim(builder_id):
        order.append('batch')
        return first
    def interactive_claim():
        order.append('interactive')
        return interactive
    def interactive_process(*args):
        stop.set()
    with patch.object(builder, 'claim_job_for_building', side_effect=batch_claim), patch.object(api, 'enabled', return_value=True), patch.object(api, 'request', return_value={}), patch.object(api, 'claim', side_effect=interactive_claim), patch.object(builder, 'run_interactive', side_effect=interactive_process), patch.object(builder, 'download_job_archive'), patch.object(builder, 'extract_job_archive', return_value=str(tmp_path)), patch.object(builder, 'find_project_dir', return_value=str(tmp_path)), patch.object(builder, 'build_push_and_clean', return_value=None), patch.object(builder, 'notify_scheduler_job_ready', return_value=True), patch.object(builder.shutil, 'rmtree'):
        builder.worker_loop(MagicMock(), stop)
    assert order == ['batch', 'interactive']
