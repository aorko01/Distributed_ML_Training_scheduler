"""Opt-in, real registry/digest tests for both interactive source types."""
import io
import os
import subprocess
from unittest.mock import patch
import uuid
import zipfile
import docker
import pytest
import interactive_build as build
import interactive_api as api
from docker_ops import docker_login

pytestmark = [pytest.mark.real_dockerhub, pytest.mark.skipif(os.getenv('RUN_INTERACTIVE_REGISTRY_E2E') != '1', reason='Opt in with RUN_INTERACTIVE_REGISTRY_E2E=1 on the runtime test host')]


def test_upload_and_derived_image_pulled_by_digest(tmp_path):
    client = docker.from_env()
    assert os.getenv('DOCKER_HUB_PASSWORD'), 'Registry test requires credentials'
    assert docker_login(client)
    # CLI operations use host-local Docker config. Password is passed on stdin.
    subprocess.run(['docker', 'login', '--username', os.environ['DOCKER_HUB_USERNAME'], '--password-stdin'],
                   input=os.environ['DOCKER_HUB_PASSWORD'], text=True, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('requirements.txt', '')
        archive.writestr('probe.py', 'import torch; print("interactive_probe_ok")')
        archive.writestr('marker.txt', 'preserved-source-files')
    response = type('Response', (), {
        '__enter__': lambda self: self, '__exit__': lambda *args: None,
        'raise_for_status': lambda self: None,
        'iter_content': lambda self, size: [data.getvalue()],
    })()
    source = {'id': str(uuid.uuid4()), 'workspace_id': str(uuid.uuid4()), 'attempt_id': str(uuid.uuid4()),
              'origin': 'UPLOAD', 'source_object_key': 'test/private-key', 'source_job_id': None,
              'base_image': 'pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime'}
    images = []
    # Exact tags/digests are written for deliberate registry retention review.
    manifest = tmp_path / 'interactive-registry-images.json'
    try:
        with patch('requests.get', return_value=response), patch.object(api, 'log'):
            uploaded = build.build(client, source, lambda: False)
        images.append(uploaded)
        derived_item = {**source, 'id': str(uuid.uuid4()), 'attempt_id': str(uuid.uuid4()), 'origin': 'EXISTING_JOB',
                        'source_image_tag': uploaded['image_tag'], 'source_job_id': str(uuid.uuid4())}
        with patch.object(api, 'log'):
            derived = build.build(client, derived_item, lambda: False)
        images.append(derived)
        for record in images:
            # Actually pull/run digest; do not substitute a local tag/config ID.
            client.images.pull(record['image_digest_ref'])
            probe = client.containers.run(record['image_digest_ref'], command=['python', '/workspace/probe.py'],
                                          network_mode='none', cap_drop=['ALL'], security_opt=['no-new-privileges:true'], remove=True)
            assert b'interactive_probe_ok' in probe
            files = client.containers.run(record['image_digest_ref'], command=['python', '-c',
                'from pathlib import Path; import shutil; assert Path("/workspace/marker.txt").read_text()=="preserved-source-files"; '
                'assert all(shutil.which(x) is None for x in ("tailscale","tailscaled","sshd","docker")); '
                'assert not Path("/var/run/docker.sock").exists(); assert not Path("/service/interactive_access").exists()'],
                network_mode='none', remove=True)
            assert files == b''
    finally:
        import json
        manifest.write_text(json.dumps(images, indent=2))
        for image in images:
            for reference in (image['image_tag'], image['image_digest_ref']):
                try:
                    client.images.remove(reference)
                except docker.errors.APIError:
                    pass
        client.close()
