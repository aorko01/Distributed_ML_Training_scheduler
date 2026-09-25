"""Snapshot import verifies an archive before publishing its exact image."""
import gzip
import hashlib
import io
import json
import tarfile
from unittest.mock import MagicMock, patch

import pytest
import interactive_build as build


ID = '12345678-1234-5678-1234-567812345678'
CONFIG_BYTES = b'{"architecture":"amd64","os":"linux"}'
CONFIG_ID = hashlib.sha256(CONFIG_BYTES).hexdigest()


def archive_bytes(tags=None, config=CONFIG_ID):
    manifest = json.dumps([{'Config': config + '.json',
                            'RepoTags': tags or ['dml-snapshot-' + ID + ':capture'],
                            'Layers': []}]).encode()
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        entry = tarfile.TarInfo('manifest.json')
        entry.size = len(manifest)
        archive.addfile(entry, io.BytesIO(manifest))
        config_entry = tarfile.TarInfo(config + '.json')
        config_entry.size = len(CONFIG_BYTES)
        archive.addfile(config_entry, io.BytesIO(CONFIG_BYTES))
    return gzip.compress(stream.getvalue())


def oci_archive_bytes():
    config_path = 'blobs/sha256/' + CONFIG_ID
    oci_manifest = b'{"schemaVersion":2,"config":{"digest":"sha256:' + CONFIG_ID.encode() + b'"}}'
    image_id = hashlib.sha256(oci_manifest).hexdigest()
    manifest = json.dumps([{'Config': config_path,
                            'RepoTags': ['dml-snapshot-' + ID + ':capture'],
                            'Layers': []}]).encode()
    index = json.dumps({'manifests': [{'digest': 'sha256:' + image_id}]}).encode()
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        for name, value in [('manifest.json', manifest), ('index.json', index),
                            (config_path, CONFIG_BYTES),
                            ('blobs/sha256/' + image_id, oci_manifest)]:
            entry = tarfile.TarInfo(name)
            entry.size = len(value)
            archive.addfile(entry, io.BytesIO(value))
    return gzip.compress(stream.getvalue()), image_id


def item(data):
    return {
        'id': ID, 'workspace_id': ID, 'attempt_id': ID, 'origin': 'SNAPSHOT',
        'snapshot_operation_id': ID, 'snapshot_sha256': hashlib.sha256(data).hexdigest(),
        'snapshot_size': len(data), 'platform': 'linux/amd64',
        'snapshot_image_id': 'sha256:' + CONFIG_ID,
        'user': '10001:10001', 'workdir': '/workspace',
        'source_ssh_profile': 'v1', 'source_developer_profile': 'v1',
        'source_runtime_revision_id': ID,
    }


def client(arch='amd64', user='10001:10001', volumes=None, loaded_id=CONFIG_ID):
    docker = MagicMock()
    image = MagicMock(id='sha256:' + loaded_id)
    image.attrs = {
        'Os': 'linux', 'Architecture': arch,
        'Config': {'User': user, 'WorkingDir': '/workspace', 'Volumes': volumes,
                   'Entrypoint': ['/bin/sh'], 'Cmd': ['/bin/true'],
                   'Labels': {'io.dml.developer-profile': 'v1',
                              'io.dml.vscode-ssh-profile': 'v1',
                              'io.dml.workspace': ID, 'io.dml.revision': ID}},
    }
    docker.images.get.return_value = image
    docker.images.get_registry_data.return_value.attrs = {
        'Descriptor': {'digest': 'sha256:' + 'c' * 64},
    }
    docker.api.build.return_value = [{}]
    docker.containers.create.return_value.get_archive.return_value = (iter(()), {})
    return docker


def import_archive(data, docker=None, work=None, **kwargs):
    docker = docker or client()
    work = work or item(data)
    with patch.object(build, 'download', return_value=iter([data])), \
         patch.object(build, 'run_command', return_value='') as commands:
        result = build.import_snapshot(work, docker, 'linux/amd64',
                                       '10001:10001', '/workspace', **kwargs)
    return result, commands, docker


def test_snapshot_import_streams_archive_and_retains_ssh_profile():
    data = archive_bytes()
    result, commands, docker = import_archive(data)
    assert commands.call_args_list[0].args[0][:2] == ['load', '--input']
    assert commands.call_args_list[-1].args[0][0] == 'pull'
    assert result['digest_ref'].endswith('@sha256:' + 'c' * 64)
    assert result['developer_profile'] == result['ssh_profile'] == 'v1'
    assert docker.api.tag.call_args_list[-1].args[0] == 'sha256:' + CONFIG_ID


def test_oci_archive_uses_index_image_id_not_config_blob_id():
    data, image_id = oci_archive_bytes()
    work = item(data)
    work['snapshot_image_id'] = 'sha256:' + image_id
    result, _, docker = import_archive(data, client(loaded_id=image_id), work)
    assert result['digest_ref'].endswith('@sha256:' + 'c' * 64)
    assert docker.api.tag.call_args_list[-1].args[0] == 'sha256:' + image_id


@pytest.mark.parametrize('change', ['hash', 'extra_tag', 'identity', 'arch', 'user', 'volume'])
def test_snapshot_rejects_corrupt_or_wrong_image(change):
    data = archive_bytes(['other:tag']) if change == 'extra_tag' else archive_bytes()
    work = item(data)
    docker = client()
    if change == 'hash':
        work['snapshot_sha256'] = '0' * 64
    elif change == 'identity':
        docker = client(loaded_id='d' * 64)
    elif change == 'arch':
        docker = client(arch='arm64')
    elif change == 'user':
        docker = client(user='0')
    elif change == 'volume':
        docker = client(volumes={'/workspace': {}})
    with pytest.raises(build.BuildFailure):
        import_archive(data, docker, work)


def test_derived_training_image_publishes_child_id():
    data = archive_bytes()
    docker = client()
    parent = docker.images.get.return_value
    child = MagicMock(id='sha256:' + 'd' * 64)
    docker.images.get.side_effect = [parent, parent, parent, child]
    result, _, docker = import_archive(
        data, docker, training_command=['python', 'train.py', '--epochs', '10'],
    )
    assert result['digest_ref'].endswith('@sha256:' + 'c' * 64)
    assert docker.api.tag.call_args_list[-1].args[0] == child.id
    context = docker.api.build.call_args.kwargs['fileobj']
    context.seek(0)
    assert 'CMD ["python","train.py","--epochs","10"]' in context.read().decode()
