"""Authenticated interactive queue, intentionally separate from batch callbacks."""
import os
import stat
import requests
from config import SCHEDULER_BASE_URL, BUILDER_ID


def enabled():
    return bool(os.getenv('INTERACTIVE_BUILDER_SECRET_FILE'))


def headers():
    path = os.environ['INTERACTIVE_BUILDER_SECRET_FILE']
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o027 or info.st_size > 256:
            raise ValueError('unsafe builder secret file')
        token = os.read(fd, 257).decode('ascii').strip()
    finally:
        os.close(fd)
    if len(token) < 32 or len(token) > 256 or len(set(token)) < 8:
        raise ValueError('invalid builder credential')
    return {'Authorization': 'Bearer ' + token}


def request(operation, payload):
    response = requests.post(SCHEDULER_BASE_URL.rstrip('/') + '/internal/interactive/builds/' + operation,
                             headers=headers(), json=payload, timeout=10)
    response.raise_for_status()
    return response.json()


def claim():
    return request('claim', {'builder_id': BUILDER_ID})['work_item']


def snapshot_descriptor(operation_id):
    response = requests.get(
        SCHEDULER_BASE_URL.rstrip('/') + '/internal/interactive/builds/snapshot/' + operation_id,
        headers=headers(), timeout=10,
    )
    response.raise_for_status()
    return response.json()


def attempt(item):
    return {'builder_id': BUILDER_ID, 'revision_id': item['id'], 'attempt_id': item['attempt_id']}


def log(item, line):
    request('logs', {**attempt(item), 'lines': [line]})
