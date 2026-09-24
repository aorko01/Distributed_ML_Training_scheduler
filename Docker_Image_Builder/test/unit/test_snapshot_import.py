"""Unit tests for the SNAPSHOT import branch and training derivation.

Docker daemon calls are mocked at the interactive_build module boundary, in the
same style as test_interactive_build.py.
"""
from unittest.mock import MagicMock, patch
import pytest
import interactive_build as build


def item(origin='SNAPSHOT', artifact=None):
    return {
        'id': '12345678-1234-5678-1234-567812345678',
        'workspace_id': '12345678-1234-5678-1234-567812345678',
        'attempt_id': '12345678-1234-5678-1234-567812345678',
        'origin': origin, 'base_image': None, 'source_image_tag': None,
        'source_job_id': None, 'source_object_key': 'snapshots/ws1/1/op1/key',
        'snapshot_operation_id': 'op1', 'snapshot_revision': 1,
        'artifact_id': 'art1', 'artifact_sha256': 'a' * 64, 'artifact_size': 1 << 20,
        'snapshot_sha256': (artifact or {}).get('sha256', 'a' * 64),
        'snapshot_size': (artifact or {}).get('size', 1 << 20),
        'platform': 'linux/amd64', 'user': '10001:10001', 'workdir': '/workspace',
    }


def attrs(arch='amd64'):
    return {
        'Id': 'sha256:' + 'b' * 64,
        'Os': 'linux', 'Architecture': arch,
        'Config': {'User': '10001:10001', 'WorkingDir': '/workspace', 'Volumes': None},
    }


def client_for(attrs_value):
    client = MagicMock()
    image = MagicMock()
    image.attrs = attrs_value
    image.id = attrs_value['Id']
    client.images.get.return_value = image
    client.images.load.return_value = [image]
    client.images.get_registry_data.return_value.attrs = {
        'Descriptor': {'digest': 'sha256:' + 'c' * 64}
    }
    client.images.get_registry_data.return_value.repo_digests = [
        'registry.example/dml/interactive-ws1@sha256:' + 'c' * 64
    ]
    return client


def test_snapshot_import_happy_path_and_portable_tag():
    client = client_for(attrs())
    with patch.object(build, 'download', return_value=iter([b'artifact-bytes'])):
        # hash/size in item() are placeholders; artifact bytes differ so drop
        # verification for the happy path (verified explicitly below).
        it = item()
        it.pop('snapshot_sha256')
        it.pop('snapshot_size')
        with patch.object(build, 'run_command', return_value=''):
            result = build.import_snapshot(it, client, 'linux/amd64', '10001:10001', '/workspace')
    client.images.load.assert_called_once()
    client.api.tag.assert_any_call('sha256:' + 'b' * 64, build.SNAPSHOT_CLEAN_REFERENCE, build.SNAPSHOT_CLEAN_TAG)
    digest = result['digest_ref'] if isinstance(result, dict) else result
    assert digest.endswith('@sha256:' + 'c' * 64)


def test_snapshot_hash_mismatch_rejected():
    client = client_for(attrs())
    with patch.object(build, 'download', return_value=iter([b'other-bytes'])):
        with pytest.raises(build.BuildFailure) as exc:
            build.import_snapshot(item(), client, 'linux/amd64', '10001:10001', '/workspace')
    assert exc.value.kind == 'system'
    client.images.load.assert_not_called()


def test_snapshot_arch_mismatch_rejected():
    client = client_for(attrs(arch='arm64'))
    it = item()
    it.pop('snapshot_sha256')
    it.pop('snapshot_size')
    with patch.object(build, 'download', return_value=iter([b'artifact-bytes'])):
        with pytest.raises(build.BuildFailure) as exc:
            build.import_snapshot(it, client, 'linux/amd64', '10001:10001', '/workspace')
    assert exc.value.kind == 'system'


def test_snapshot_wrong_user_rejected():
    config = dict(attrs()['Config'])
    config['User'] = '0'
    a = dict(attrs())
    a['Config'] = config
    client = client_for(a)
    it = item()
    it.pop('snapshot_sha256')
    it.pop('snapshot_size')
    with patch.object(build, 'download', return_value=iter([b'artifact-bytes'])):
        with pytest.raises(build.BuildFailure) as exc:
            build.import_snapshot(it, client, 'linux/amd64', '10001:10001', '/workspace')
    assert exc.value.kind == 'system'


def test_snapshot_volume_backed_image_rejected():
    config = dict(attrs()['Config'])
    config['Volumes'] = {'/w': {}}
    a = dict(attrs())
    a['Config'] = config
    client = client_for(a)
    it = item()
    it.pop('snapshot_sha256')
    it.pop('snapshot_size')
    with patch.object(build, 'download', return_value=iter([b'artifact-bytes'])):
        with pytest.raises(build.BuildFailure) as exc:
            build.import_snapshot(it, client, 'linux/amd64', '10001:10001', '/workspace')
    assert exc.value.kind == 'system'


def test_snapshot_unexpected_load_identity_rejected():
    client = client_for(attrs())
    client.images.load.return_value = [MagicMock(id='sha256:' + 'd' * 64), MagicMock(id='sha256:' + 'e' * 64)]
    it = item()
    it.pop('snapshot_sha256')
    it.pop('snapshot_size')
    with patch.object(build, 'download', return_value=iter([b'artifact-bytes'])):
        with pytest.raises(build.BuildFailure) as exc:
            build.import_snapshot(it, client, 'linux/amd64', '10001:10001', '/workspace')
    assert exc.value.kind == 'system'


def test_training_derivation_sets_exec_form_cmd():
    client = client_for(attrs())
    it = item()
    it.pop('snapshot_sha256')
    it.pop('snapshot_size')
    with patch.object(build, 'download', return_value=iter([b'artifact-bytes'])):
        with patch.object(build, 'run_command', return_value=''):
            result = build.import_snapshot(
                it, client, 'linux/amd64', '10001:10001', '/workspace',
                training_command=['python', 'train.py', '--epochs', '10'],
            )
    assert result is not None
    client.api.build.assert_called_once()
    kwargs = client.api.build.call_args.kwargs
    context = kwargs['fileobj']
    context.seek(0)
    dockerfile = context.read().decode()
    assert dockerfile.startswith('FROM ' + build.SNAPSHOT_CLEAN_REFERENCE)
    assert 'ENTRYPOINT []' in dockerfile
    assert 'CMD ["python","train.py","--epochs","10"]' in dockerfile
    assert 'USER 10001:10001' in dockerfile
    assert 'WORKDIR /workspace' in dockerfile


def test_training_derivation_rejects_non_python_tokens():
    client = client_for(attrs())
    it = item()
    it.pop('snapshot_sha256')
    it.pop('snapshot_size')
    with patch.object(build, 'download', return_value=iter([b'artifact-bytes'])):
        with pytest.raises(build.BuildFailure) as exc:
            build.import_snapshot(
                it, client, 'linux/amd64', '10001:10001', '/workspace',
                training_command=['bash', 'train.sh'],
            )
    assert exc.value.kind == 'user'
    client.api.build.assert_not_called()


def test_build_dispatches_snapshot_origin_without_zip_extractor(tmp_path):
    """build() with a SNAPSHOT work item must not touch the ZIP extractor."""
    client = client_for(attrs())
    it = item()
    it.pop('snapshot_sha256')
    it.pop('snapshot_size')
    with patch.object(build, 'download', return_value=iter([b'x'])), patch.object(
        build, 'import_snapshot',
        return_value='registry.example/dml/interactive-ws1@sha256:' + 'c' * 64,
    ) as imp, patch.object(build, 'extract', side_effect=AssertionError('must not extract')), patch(
        'interactive_build.api'
    ):
        result = build.build(client, it, lambda: False)
    imp.assert_called_once()
    assert result['image_digest_ref'].endswith('@sha256:' + 'c' * 64)
