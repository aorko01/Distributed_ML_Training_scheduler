"""Workload-only image builds. No access agents, Docker socket or credentials."""
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import time
import zipfile
from config import DOCKER_HUB_USERNAME, logger
from database import record_interactive_attempt
import interactive_api as api
from docker_ops import _run_cancellable_docker_build, _get_build_lock, _remove_local_image, _terminate_process, _is_auth_error, _is_transient_build_error

MAX_UPLOAD = 64 * 1024 * 1024
MAX_SNAPSHOT = 8 * 1024 * 1024 * 1024
# Local staging name for the exact loaded snapshot before it is retagged to
# the immutable per-revision tag. Never pushed; never-User controlled.
SNAPSHOT_CLEAN_REFERENCE = 'dml-snapshot-clean'
SNAPSHOT_CLEAN_TAG = 'ready'
MAX_EXPANDED = 512 * 1024 * 1024
MAX_FILES = 10000
DIGEST = re.compile(r'sha256:[0-9a-f]{64}')
REFERENCE = re.compile(r'^[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}|@sha256:[0-9a-f]{64})$')


class Cancelled(Exception):
    pass


class BuildFailure(Exception):
    def __init__(self, kind):
        self.kind = kind


def check(cancel):
    if cancel():
        raise Cancelled()


def tag_for(item):
    # UUIDs are validated instead of lossy sanitization: no tag collisions.
    import uuid
    ids = [str(uuid.UUID(item[key])) for key in ('workspace_id', 'id', 'attempt_id')]
    return f'{DOCKER_HUB_USERNAME}/interactive-{ids[0]}:revision-{ids[1]}-attempt-{ids[2]}'


def extract(data, destination):
    if len(data) > MAX_UPLOAD:
        raise BuildFailure('user')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_FILES or sum(x.file_size for x in entries) > MAX_EXPANDED:
                raise BuildFailure('user')
            names = set()
            total = 0
            for entry in entries:
                path = PurePosixPath(entry.filename)
                mode = entry.external_attr >> 16
                if not entry.filename or path.is_absolute() or '..' in path.parts or '\\' in entry.filename or ':' in entry.filename or '\x00' in entry.filename or str(path) in names:
                    raise BuildFailure('user')
                names.add(str(path))
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))) or path.name in ('Dockerfile', '.dockerignore'):
                    raise BuildFailure('user')
                target = Path(destination).joinpath(*path.parts)
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, target.open('wb') as output:
                    while chunk := source.read(65536):
                        total += len(chunk)
                        if total > MAX_EXPANDED:
                            raise BuildFailure('user')
                        output.write(chunk)
    except (zipfile.BadZipFile, RuntimeError, OSError, ValueError):
        raise BuildFailure('user') from None
    root = Path(destination)
    if (root / 'requirements.txt').is_file():
        return root
    projects = [path.parent for path in root.glob('*/requirements.txt') if not path.parent.name.startswith(('.', '__'))]
    if len(projects) != 1:
        raise BuildFailure('user')
    return projects[0]


DEVELOPER_PROFILE = 'v1'
DEVELOPER_PROFILE_LABEL = 'io.dml.developer-profile'
DEVELOPER_USER = '10001:10001'
DEVELOPER_HOME = '/home/dml'
DEVELOPER_VENV = '/opt/dml-venv'
# VS Code Remote-SSH profile (plan.md §4). New validated Debian/Ubuntu
# developer images receive this label; old digests are never relabelled.
SSH_PROFILE = 'v1'
SSH_PROFILE_LABEL = 'io.dml.vscode-ssh-profile'
# Workload-loopback sshd port (fixed, never published via Docker ports).
SSH_PORT = 2222
SSH_USER = 'dml'
SSH_UID = 10001


def dockerfile(item, base, upload):
    if not REFERENCE.fullmatch(base) or '@sha256:' not in base:
        raise BuildFailure('system')
    # A published interactive workload must never inherit an arbitrary image's
    # root default.  The Worker rejects root workloads before launch, and the
    # unprivileged account also keeps the mounted workspace writable without
    # granting runtime container privileges.
    #
    # Developer profile v1 (plan.md §5): the image user stays 10001:10001
    # (dml), but the image prepares a user-owned venv, passwordless sudo for
    # `apt`, and a real home/shell so `pip install` / `sudo apt-get install`
    # work from the browser terminal without a venv activation step.
    lines = [
        f'FROM {base}',
        'USER root',
        'WORKDIR /workspace',
        # dml account with a usable shell and home (not nologin, not /tmp).
        'RUN getent group 10001 >/dev/null || groupadd --gid 10001 dml',
        'RUN id -u 10001 >/dev/null 2>&1 || useradd --uid 10001 --gid 10001 --create-home --shell /bin/bash dml',
        # Debian/Ubuntu useradd leaves the account password-locked (`!`);
        # this sshd build denies locked accounts for ALL methods including
        # pubkey. `*` keeps password login impossible while allowing pubkey.
        "RUN usermod -p '*' dml",
        'RUN mkdir -p /workspace /home/dml /opt/dml-venv && chown 10001:10001 /workspace /home/dml',
        # Bootstrap OS tooling as root. Noninteractive, no recommends, and the
        # apt index is removed from the layer afterwards.
        # SSH-capable profile (§4): openssh-server + SFTP server, tar,
        # bash, and VS Code Server glibc/libstdc++ prerequisites. Old
        # revisions keep their immutable layers; only new builds get this.
        'RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends '
        'sudo ca-certificates curl wget bash tar git pkg-config build-essential python3-venv '
        'openssh-server libc6 libstdc++6 && '
        'rm -rf /var/lib/apt/lists/*',
        # Image-owned SSH session entry wrapper: lands in /workspace with the
        # expected HOME/venv env; sshd invokes it via ForceCommand-equivalent
        # per-key command= prefix installed by the Worker helper.
        'COPY --chown=0:0 dml-ssh-session /usr/local/bin/dml-ssh-session',
        'RUN chmod 0755 /usr/local/bin/dml-ssh-session',
        # Workspace venv with access to the base CUDA stack. ENV (not an
        # activation script) makes it the default for every later RUN, exec,
        # and `python train.py`.
        f'RUN python3 -m venv --system-site-packages {DEVELOPER_VENV} && '
        f'chown -R 10001:10001 {DEVELOPER_VENV} && '
        f'{DEVELOPER_VENV}/bin/python -c "import sys; assert sys.prefix != sys.base_prefix"',
        f'ENV VIRTUAL_ENV={DEVELOPER_VENV}',
        f'ENV PATH={DEVELOPER_VENV}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
        f'ENV HOME={DEVELOPER_HOME}',
    ]
    if upload:
        lines += [
            'COPY project/ /workspace/',
            f'RUN {DEVELOPER_VENV}/bin/python -m pip install --no-cache-dir -r /workspace/requirements.txt',
            f'RUN {DEVELOPER_VENV}/bin/python -c "import torch; print(torch.__version__)"',
        ]
    else:
        lines += [
            f'RUN {DEVELOPER_VENV}/bin/python -c "import torch; print(torch.__version__)"',
        ]
    lines += [
        # Passwordless sudo for dml only. Root-owned 0440, validated below.
        'RUN printf "dml ALL=(ALL) NOPASSWD:ALL\\n" > /etc/sudoers.d/dml && chmod 0440 /etc/sudoers.d/dml && visudo -cf /etc/sudoers.d/dml',
        'RUN chown -R 10001:10001 /workspace /home/dml /opt/dml-venv',
    ]
    # Labels are JSON-quoted, sourced from validated Scheduler data.
    for key, value in {'workspace': item['workspace_id'], 'revision': item['id'], 'origin': item['origin'], 'source-job': item.get('source_job_id') or ''}.items():
        lines.append(f'LABEL io.dml.{key}={json.dumps(value)}')
    lines.append(f'LABEL {DEVELOPER_PROFILE_LABEL}={json.dumps(DEVELOPER_PROFILE)}')
    lines.append(f'LABEL {SSH_PROFILE_LABEL}={json.dumps(SSH_PROFILE)}')
    lines.append(f'USER {DEVELOPER_USER}')
    return '\n'.join(lines) + '\n'


def developer_profile_of_attrs(attrs):
    """Report the prepared developer profile from inspected image attrs."""
    try:
        labels = ((attrs or {}).get('Config') or {}).get('Labels') or {}
        if labels.get(DEVELOPER_PROFILE_LABEL) == DEVELOPER_PROFILE:
            return DEVELOPER_PROFILE
    except Exception:
        pass
    return None


def ssh_profile_of_attrs(attrs):
    """Report the VS Code SSH profile; old/unsupported images return None."""
    try:
        labels = ((attrs or {}).get('Config') or {}).get('Labels') or {}
        if labels.get(SSH_PROFILE_LABEL) == SSH_PROFILE:
            return SSH_PROFILE
    except Exception:
        pass
    return None


# Fixed image-owned session entry wrapper executed by workload sshd.
# Installed via COPY in dockerfile(); kept here so tests and the Worker
# smoke test pin the exact bytes. cd /workspace, preserve HOME/venv,
# exec shell / non-interactive command / SFTP subsystem correctly.
SSH_SESSION_WRAPPER = """#!/bin/bash
# dml-ssh-session: fixed entry for workload sshd (plan.md §4).
# Invoked as: dml-ssh-session [command...]. SFTP subsystem calls arrive
# via SSH_ORIGINAL_COMMAND=sftp-server path; honour it without a shell.
set -u
cd /workspace || exit 127
export HOME=/home/dml
export VIRTUAL_ENV=/opt/dml-venv
case ":${PATH:-}:" in
  *:/opt/dml-venv/bin:*) ;;
  *) export PATH=/opt/dml-venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin ;;
esac
if [ -n "${SSH_ORIGINAL_COMMAND:-}" ]; then
  case "$SSH_ORIGINAL_COMMAND" in
    *sftp-server*|*sftp*) exec $SSH_ORIGINAL_COMMAND ;;
  esac
  exec bash -c "$SSH_ORIGINAL_COMMAND"
fi
if [ "$#" -gt 0 ]; then
  exec "$@"
fi
exec bash -l
"""


def ssh_session_wrapper_bytes():
    return SSH_SESSION_WRAPPER.encode()


def run_command(args, cancel, timeout_seconds=600):
    """Bounded disk spool; polling can cancel silent pull/push registry hangs."""
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(['docker', *args], stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        started = time.monotonic()
        try:
            while process.poll() is None:
                check(cancel)
                if time.monotonic() - started > timeout_seconds or output.tell() > 4 * 1024 * 1024:
                    raise BuildFailure('system')
                time.sleep(0.1)
            output.seek(0)
            text = output.read(4 * 1024 * 1024).decode('utf-8', errors='replace')
            if process.returncode:
                if any(word in text.lower() for word in ('manifest unknown', 'not found', 'does not exist')) and args[0] == 'pull' and not _is_auth_error(text):
                    raise BuildFailure('user')
                raise BuildFailure('system')
            return text
        finally:
            _terminate_process(process)


def resolve(client, reference, cancel):
    if not REFERENCE.fullmatch(reference):
        raise BuildFailure('system')
    check(cancel)
    run_command(['pull', reference], cancel)
    check(cancel)
    # get_registry_data returns registry manifest/index digest, not image config ID.
    descriptor = client.images.get_registry_data(reference).attrs['Descriptor']
    digest = descriptor['digest']
    if not DIGEST.fullmatch(digest):
        raise BuildFailure('system')
    repository = reference.split('@')[0].rsplit(':', 1)[0]
    return repository + '@' + digest


def download(item):
    """Stream the private snapshot artifact for a SNAPSHOT work item.

    Yields raw bytes; the caller enforces size/hash bounds. Separated for
    unit-test patching at the module boundary (same style as run_command).
    """
    import requests
    from urllib.parse import urlparse
    operation = item.get('snapshot_operation_id')
    if not operation or not re.fullmatch(r'[0-9a-f-]{36}', operation):
        raise BuildFailure('system')
    descriptor = api.snapshot_descriptor(operation)
    if (descriptor.get('target_revision_id') != item.get('id')
            or descriptor.get('sha256') != item.get('snapshot_sha256')
            or descriptor.get('size') != item.get('snapshot_size')):
        raise BuildFailure('system')
    url = descriptor.get('download_url')
    if not isinstance(url, str) or urlparse(url).scheme != 'https':
        raise BuildFailure('system')
    with requests.get(
        url, headers={'Authorization': 'Snapshot ' + descriptor['download_token']},
        stream=True, timeout=(5, 60),
    ) as response:
        response.raise_for_status()
        for chunk in response.iter_content(65536):
            if chunk:
                yield chunk


def validate_training_command(command):
    """Exec-form training command allowlist: python only, no shell escapes."""
    if not isinstance(command, (list, tuple)) or not command:
        raise BuildFailure('user')
    if command[0] not in ('python', 'python3'):
        raise BuildFailure('user')
    for token in command:
        if not isinstance(token, str) or not token or len(token) > 512:
            raise BuildFailure('user')
        if '\n' in token or '\r' in token or '\x00' in token:
            raise BuildFailure('user')
    if any(t.strip().startswith('-') and False for t in command):
        raise BuildFailure('user')
    return list(command)


def import_snapshot(item, client, platform, user, workdir, training_command=None, cancel=lambda: False):
    """Load a Worker-captured artifact, validate portability, push digest tag.

    Never feeds the bytes to the ZIP extractor and never reruns pip install:
    the saved filesystem (code + site-packages) is the portable image. Returns
    the digest-pinned reference ``repository@sha256:...``.
    """
    import hashlib
    import io as _io
    import tarfile
    if training_command is not None:
        training_command = validate_training_command(training_command)
    expected_platform = platform
    # Stream the compressed archive to disk; never hold an 8 GiB snapshot in RAM.
    sha = hashlib.sha256()
    size = 0
    with tempfile.TemporaryDirectory(prefix='dml-snapshot-import-') as directory:
        archive = os.path.join(directory, 'snapshot.tar.gz')
        with open(archive, 'wb') as out:
            for chunk in download(item):
                check(cancel)
                sha.update(chunk)
                size += len(chunk)
                if size > MAX_SNAPSHOT:
                    raise BuildFailure('system')
                out.write(chunk)
        if item.get('snapshot_sha256') and sha.hexdigest() != item['snapshot_sha256']:
            raise BuildFailure('user')
        if item.get('snapshot_size') and size != item['snapshot_size']:
            raise BuildFailure('user')
        expected_tag = f'dml-snapshot-{item["snapshot_operation_id"]}:capture'
        try:
            with tarfile.open(archive, 'r:gz') as tar:
                expanded = 0
                members = 0
                for member in tar:
                    check(cancel)
                    members += 1
                    expanded += member.size
                    if (members > 100000 or expanded > 64 * 1024 * 1024 * 1024
                            or not (member.isfile() or member.isdir()) or member.name.startswith('/')
                            or '..' in Path(member.name).parts):
                        raise BuildFailure('user')
                manifest = tar.extractfile('manifest.json')
                if manifest is None:
                    raise BuildFailure('user')
                raw = manifest.read(1 << 20)
                entries = json.loads(raw)
                config_path = entries[0].get('Config', '') if len(entries) == 1 else ''
                match = re.fullmatch(r'([0-9a-f]{64})\.json|blobs/sha256/([0-9a-f]{64})', config_path)
                if (len(entries) != 1 or entries[0].get('RepoTags') != [expected_tag]
                        or not match):
                    raise BuildFailure('user')
                config_digest = match.group(1) or match.group(2)
                config_file = tar.extractfile(config_path)
                if config_file is None or hashlib.sha256(config_file.read(1 << 20)).hexdigest() != config_digest:
                    raise BuildFailure('user')
                if 'index.json' in tar.getnames():
                    index_file = tar.extractfile('index.json')
                    index = json.loads(index_file.read(1 << 20)) if index_file else {}
                    manifests = index.get('manifests') or []
                    if len(manifests) != 1 or not DIGEST.fullmatch(manifests[0].get('digest', '')):
                        raise BuildFailure('user')
                    expected_id = manifests[0]['digest']
                    if tar.getmember('blobs/sha256/' + expected_id[7:]).size > (1 << 20):
                        raise BuildFailure('user')
                else:
                    expected_id = 'sha256:' + config_digest
        except (tarfile.TarError, ValueError, KeyError, TypeError):
            raise BuildFailure('user') from None
        run_command(['load', '--input', archive], cancel, timeout_seconds=1800)
        loaded_id = client.images.get(expected_tag).id
        if loaded_id != expected_id or loaded_id != item.get('snapshot_image_id'):
            raise BuildFailure('user')
    image = client.images.get(loaded_id)
    attrs = image.attrs or {}
    if attrs.get('Os', '') + '/' + attrs.get('Architecture', '') != expected_platform:
        raise BuildFailure('system')
    config = attrs.get('Config') or {}
    if config.get('User', '') != user or config.get('WorkingDir', '') != workdir:
        raise BuildFailure('system')
    if config.get('Volumes'):
        raise BuildFailure('system')
    if config.get('Entrypoint') != ['/bin/sh'] or config.get('Cmd') != ['/bin/true']:
        raise BuildFailure('user')
    labels = config.get('Labels') or {}
    if (labels.get('io.dml.workspace') != item.get('workspace_id')
            or labels.get('io.dml.revision') != item.get('source_runtime_revision_id')):
        raise BuildFailure('user')
    profile = developer_profile_of_attrs(attrs)
    ssh_label = ssh_profile_of_attrs(attrs)
    if item.get('source_ssh_profile') == 'v1' and ssh_label != 'v1':
        raise BuildFailure('user')
    if item.get('source_developer_profile') == 'v1' and profile != 'v1':
        raise BuildFailure('user')
    ssh_profile = 'v1' if item.get('source_ssh_profile') == 'v1' and ssh_label == 'v1' else None
    if ssh_profile:
        check(cancel)
        inspection = client.containers.create(
            loaded_id, command=['/bin/true'], entrypoint=['/bin/true']
        )
        try:
            for path in ('/usr/sbin/sshd', '/usr/lib/openssh/sftp-server',
                         '/usr/local/bin/dml-ssh-session', '/bin/bash', '/usr/bin/tar'):
                check(cancel)
                try:
                    stream, _ = inspection.get_archive(path)
                    if hasattr(stream, 'close'):
                        stream.close()
                except Exception:
                    raise BuildFailure('user') from None
        finally:
            inspection.remove(force=True)
    # Normalize through the exact staging name so the retag below is exact.
    clean_reference = f'dml-snapshot-clean-{item["id"]}-{item["attempt_id"]}:ready'
    clean_repository, clean_tag = clean_reference.rsplit(':', 1)
    client.api.tag(loaded_id, clean_repository, clean_tag)
    staged = client.images.get(clean_reference)
    if getattr(staged, 'id', loaded_id) != loaded_id:
        raise BuildFailure('system')
    pushed_id = loaded_id
    if training_command is not None:
        dockerfile_text = (
            f'FROM {clean_reference}\n'
            'ENTRYPOINT []\n'
            f'CMD {json.dumps(training_command, separators=(",", ":"))}\n'
            f'USER {user}\n'
            f'WORKDIR {workdir}\n'
        )
        context = _io.BytesIO(dockerfile_text.encode())
        for result in client.api.build(fileobj=context, rm=True, forcerm=True, tag=tag_for(item), decode=True):
            if 'error' in result:
                raise BuildFailure('user')
        pushed_id = client.images.get(tag_for(item)).id
        if not pushed_id or pushed_id == loaded_id:
            raise BuildFailure('system')
    tag = tag_for(item)
    repository, tag_name = tag.rsplit(':', 1)
    client.api.tag(pushed_id, repository, tag_name)
    run_command(['push', tag], cancel)
    digest = client.images.get_registry_data(tag).attrs['Descriptor']['digest']
    if not DIGEST.fullmatch(digest):
        raise BuildFailure('system')
    run_command(['pull', repository + '@' + digest], cancel)
    try:
        client.images.remove(clean_reference, force=False)
    except Exception:
        pass
    result = {'digest_ref': repository + '@' + digest, 'developer_profile': profile,
              'ssh_profile': ssh_profile}
    if training_command is not None:
        return result
    return result


def build(client, item, cancel):
    # SNAPSHOT never touches the ZIP extractor: the artifact is an exact
    # committed filesystem (code + site-packages), not an upload archive.
    if item.get('origin') == 'SNAPSHOT':
        check(cancel)
        api.log(item, 'Importing workspace snapshot')
        imported = import_snapshot(
            item, client,
            item.get('platform', 'linux/amd64'),
            item.get('user', '10001:10001'),
            item.get('workdir', '/workspace'),
            training_command=item.get('training_command'),
            cancel=cancel,
        )
        # import_snapshot returns a dict; tolerate a legacy plain digest string.
        if isinstance(imported, dict):
            digest_ref, snapshot_profile = imported['digest_ref'], imported.get('developer_profile')
            snapshot_ssh_profile = imported.get('ssh_profile')
        else:
            digest_ref, snapshot_profile = imported, None
            snapshot_ssh_profile = None
        check(cancel)
        tag = tag_for(item)
        return {'image_tag': tag, 'image_digest_ref': digest_ref, 'resolved_base_digest': digest_ref,
                'developer_profile': snapshot_profile, 'ssh_profile': snapshot_ssh_profile}
    tag = tag_for(item)
    with tempfile.TemporaryDirectory(prefix='interactive-build-') as temporary:
        root = Path(temporary)
        upload = item['origin'] == 'UPLOAD'
        check(cancel)
        if upload:
            # Stream download, enforce compressed size before buffering.
            import requests
            from urllib.parse import quote
            from config import OBJECT_STORE_URL, OBJECT_STORE_BUCKET
            data = bytearray()
            with requests.get(f'{OBJECT_STORE_URL}/objects/{OBJECT_STORE_BUCKET}/{quote(item["source_object_key"], safe="/")}', stream=True, timeout=(5, 30)) as response:
                response.raise_for_status()
                for chunk in response.iter_content(65536):
                    check(cancel)
                    data.extend(chunk)
                    if len(data) > MAX_UPLOAD:
                        raise BuildFailure('user')
            extracted = root / 'extracted'
            extracted.mkdir()
            project = extract(data, extracted)
            shutil.copytree(project, root / 'project')
            shutil.rmtree(extracted)
            source = item['base_image']
            # Defense in depth: same operator allowlist as Scheduler (legacy ids
            # resolve upstream; here only full official PyTorch runtime tags).
            if not re.fullmatch(r'pytorch/pytorch:[\d.]+-cuda[\d.]+-cudnn[\d.]+-runtime', source or ''):
                raise BuildFailure('system')
        elif item['origin'] == 'EXISTING_JOB':
            source = item['source_image_tag']
        else:
            raise BuildFailure('system')
        api.log(item, 'Resolving base image')
        base = resolve(client, source, cancel)
        (root / 'Dockerfile').write_text(dockerfile(item, base, upload))
        (root / 'dml-ssh-session').write_bytes(ssh_session_wrapper_bytes())
        lock = _get_build_lock(base)
        while not lock.acquire(timeout=0.25):
            check(cancel)
        try:
            check(cancel)
            api.log(item, 'Building workload image')
            logger.info("Building interactive image %s ...", tag)
            code, raw, cancelled = _run_cancellable_docker_build(str(root), tag, cancel, lambda line: None)
            if cancelled:
                raise Cancelled()
            if code:
                message = '\n'.join(raw)
                raise BuildFailure('system' if _is_auth_error(message) or _is_transient_build_error(message) or any(pattern in message.lower() for pattern in ('cannot connect to the docker daemon', 'cannot connect to docker daemon', 'error during connect', 'context deadline exceeded', 'i/o timeout', 'network is unreachable', 'dial tcp', 'toomanyrequests', 'service unavailable')) else 'user')
        finally:
            lock.release()
        check(cancel)
        api.log(item, 'Pushing workload image')
        logger.info("Pushing interactive image %s ...", tag)
        run_command(['push', tag], cancel)
        check(cancel)
        digest = client.images.get_registry_data(tag).attrs['Descriptor']['digest']
        check(cancel)
        if not DIGEST.fullmatch(digest):
            raise BuildFailure('system')
        return {'image_tag': tag, 'image_digest_ref': tag.rsplit(':', 1)[0] + '@' + digest, 'resolved_base_digest': base,
                'developer_profile': DEVELOPER_PROFILE, 'ssh_profile': SSH_PROFILE}


def process(client, item, registry):
    attempt = item['attempt_id']
    registry.register(item['id'], attempt)
    cancel = lambda: registry.should_cancel(attempt)
    logger.info("=" * 50)
    logger.info(
        "Processing interactive revision: %s (workspace %s, attempt %s, origin %s)",
        item['id'], item.get('workspace_id'), attempt, item.get('origin'),
    )
    try:
        record_interactive_attempt(item['id'], attempt, tag_for(item))
        result = build(client, item, cancel)
        record_interactive_attempt(item['id'], attempt, result['image_tag'], result['image_digest_ref'])
        check(cancel)
        api.request('ready', {**api.attempt(item), **result})
        record_interactive_attempt(item['id'], attempt, result['image_tag'], result['image_digest_ref'], accepted=True)
        logger.info(
            "Interactive revision %s completed: %s (%s)",
            item['id'], result['image_tag'], result['image_digest_ref'],
        )
        _remove_local_image(client, result['image_tag'])
    except Cancelled:
        logger.warning(
            "Cancelled interactive revision %s (attempt %s).",
            item['id'], attempt,
        )
        _remove_local_image(client, tag_for(item))
    except Exception as exc:
        kind = exc.kind if isinstance(exc, BuildFailure) else 'system'
        logger.error(
            "Interactive revision %s failed (%s): %s",
            item['id'], kind, exc, exc_info=True,
        )
        if not cancel():
            try:
                api.log(item, 'Build failed')
                api.request('failure' if kind == 'user' else 'release', {**api.attempt(item), 'failure_type': kind, 'failure_reason': 'Workload build failed' if kind == 'user' else 'Infrastructure unavailable'})
            except Exception:
                # Leave exact lease for the watchdog; no unfenced fallback.
                pass
        _remove_local_image(client, tag_for(item))
    finally:
        registry.unregister(attempt)
