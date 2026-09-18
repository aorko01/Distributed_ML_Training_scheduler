"""Authenticated versioned Scheduler transport. No secret-bearing logging."""

from dataclasses import dataclass
import os
import stat
from urllib.parse import urlparse
import requests


def protected_file(path, maximum=16384):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o027
            or info.st_size > maximum
        ):
            raise ValueError("Unsafe Worker credential")
        value = os.read(fd, maximum + 1)
        if len(value) > maximum:
            raise ValueError("Unsafe Worker credential")
        return value
    finally:
        os.close(fd)


def secret_file(path):
    value = protected_file(path, 256).decode("ascii").strip()
    if not 32 <= len(value) <= 256 or len(set(value)) < 8:
        raise ValueError("Invalid Worker credential")
    return value


@dataclass(frozen=True)
class Fence:
    instance_id: str
    assignment_id: str
    attempt_token: str
    generation: int | None

    @classmethod
    def from_assignment(cls, record):
        return cls(**{key: record[key] for key in cls.__annotations__})

    def body(self):
        return dict(vars(self))


class SchedulerRejected(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__("Scheduler operation rejected (%d)" % status)


class ExecutionAPI:
    def __init__(self, url, credential_file, transport=None):
        if urlparse(url).scheme != "https":
            raise ValueError("Worker Scheduler HTTPS required")
        self.url = url.rstrip("/") + "/internal/workers/v1/"
        self.credential_file = credential_file
        self.transport = transport or requests.Session()
        self.verify = os.getenv("WORKER_SCHEDULER_CA_FILE") or True

    def call(self, operation, body):
        response = self.transport.post(
            self.url + operation,
            json=body,
            headers={"Authorization": "Bearer " + secret_file(self.credential_file)},
            timeout=10,
            verify=self.verify,
        )
        if response.status_code >= 400:
            raise SchedulerRejected(response.status_code)
        return response.json()

    def register(self, instance_id, gpu_type, inventory):
        return self.call(
            "register",
            {
                "protocol_version": 1,
                "instance_id": instance_id,
                "gpu_type": gpu_type,
                "inventory": inventory,
            },
        )

    def claim(self, request):
        return self.call("claim", request)

    def heartbeat(self, body):
        return self.call("heartbeat", body)

    def event(self, record, phase, health=None, failure_code=None):
        return self.call(
            "event",
            {
                **Fence.from_assignment(record).body(),
                "sequence": record["event_sequence"],
                "phase": phase,
                "health": health or {},
                "failure_code": failure_code,
            },
        )

    def bootstrap(self, record):
        return self.call("bootstrap", Fence.from_assignment(record).body())

    def cleanup(self, record):
        return self.call("cleanup", Fence.from_assignment(record).body())

    def result(self, record, result):
        return self.call("result", {**Fence.from_assignment(record).body(), **result})
