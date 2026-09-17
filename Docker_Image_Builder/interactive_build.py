"""Workload-only image builds. No access agents, Docker socket or credentials."""
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import time
import zipfile
from config import DOCKER_HUB_USERNAME
from database import record_interactive_attempt
import interactive_api as api
from docker_ops import _run_cancellable_docker_build, _get_build_lock, _remove_local_image, _terminate_process, _is_auth_error, _is_transient_build_error

MAX_UPLOAD = 64 * 1024 * 1024
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


def dockerfile(item, base, upload):
    if not REFERENCE.fullmatch(base) or '@sha256:' not in base:
        raise BuildFailure('system')
    lines = [f'FROM {base}']
    if upload:
        lines += ['WORKDIR /workspace', 'COPY project/ /workspace/', 'RUN pip install --no-cache-dir -r requirements.txt']
    # Labels are JSON-quoted, sourced from validated Scheduler data.
    for key, value in {'workspace': item['workspace_id'], 'revision': item['id'], 'origin': item['origin'], 'source-job': item.get('source_job_id') or ''}.items():
        lines.append(f'LABEL io.dml.{key}={json.dumps(value)}')
    return '\n'.join(lines) + '\n'


def run_command(args, cancel):
    """Bounded disk spool; polling can cancel silent pull/push registry hangs."""
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(['docker', *args], stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        started = time.monotonic()
        try:
            while process.poll() is None:
                check(cancel)
                if time.monotonic() - started > 600 or output.tell() > 4 * 1024 * 1024:
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


def build(client, item, cancel):
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
        lock = _get_build_lock(base)
        while not lock.acquire(timeout=0.25):
            check(cancel)
        try:
            check(cancel)
            api.log(item, 'Building workload image')
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
        run_command(['push', tag], cancel)
        check(cancel)
        digest = client.images.get_registry_data(tag).attrs['Descriptor']['digest']
        check(cancel)
        if not DIGEST.fullmatch(digest):
            raise BuildFailure('system')
        return {'image_tag': tag, 'image_digest_ref': tag.rsplit(':', 1)[0] + '@' + digest, 'resolved_base_digest': base}


def process(client, item, registry):
    attempt = item['attempt_id']
    registry.register(item['id'], attempt)
    cancel = lambda: registry.should_cancel(attempt)
    try:
        record_interactive_attempt(item['id'], attempt, tag_for(item))
        result = build(client, item, cancel)
        record_interactive_attempt(item['id'], attempt, result['image_tag'], result['image_digest_ref'])
        check(cancel)
        api.request('ready', {**api.attempt(item), **result})
        record_interactive_attempt(item['id'], attempt, result['image_tag'], result['image_digest_ref'], accepted=True)
        _remove_local_image(client, result['image_tag'])
    except Cancelled:
        _remove_local_image(client, tag_for(item))
    except Exception as exc:
        kind = exc.kind if isinstance(exc, BuildFailure) else 'system'
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
