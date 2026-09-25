import ipaddress
import os
import stat
from dataclasses import dataclass
from pathlib import Path


def read_secret(path):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o027 or info.st_size > 256:
            raise ValueError('unsafe token file')
        token = os.read(fd, 257).strip()
        if len(token) < 32 or len(token) > 256 or any(c < 33 or c > 126 for c in token):
            raise ValueError('invalid token')
        if len(set(token)) < 8 or b'changeme' in token.lower() or b'example' in token.lower():
            raise ValueError('default token')
        return token
    finally:
        os.close(fd)


@dataclass(frozen=True)
class Config:
    socket_path: str
    token_file: str
    runtime_id: str
    host: str = '127.0.0.1'
    port: int = 9000
    health_port: int = 9002
    capacity: int = 1
    ssh_capacity: int = 8
    open_timeout: float = 5
    broker_timeout: float = 3
    write_timeout: float = 5
    ssh_handshake_timeout: float = 10

    def validate(self):
        import re
        if not ipaddress.ip_address(self.host).is_loopback:
            raise ValueError('loopback required')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', self.runtime_id):
            raise ValueError('invalid runtime ID')
        expected = Path('/run/dml-interactive') / self.runtime_id
        for value in (self.socket_path, self.token_file):
            path = Path(value)
            if not path.is_absolute() or path.parent != expected or '..' in path.parts:
                raise ValueError('unsafe runtime path')
            # A mount may replace the file, but parents cannot redirect the path.
            if any(parent.is_symlink() for parent in path.parents) or path.is_symlink():
                raise ValueError('symlink path')
        if not 1 <= self.capacity <= 16 or not 1 <= self.ssh_capacity <= 32 or not 1 <= self.port <= 65535 or not 1 <= self.health_port <= 65535 or self.port == self.health_port:
            raise ValueError('invalid limits')
        if any(not 0 < v <= 60 for v in (self.open_timeout, self.broker_timeout, self.write_timeout, self.ssh_handshake_timeout)):
            raise ValueError('invalid deadline')

    def credentials(self):
        self.validate()
        info = os.stat(self.socket_path, follow_symlinks=False)
        if not stat.S_ISSOCK(info.st_mode):
            raise ValueError('broker unavailable')
        return read_secret(self.token_file)

    @classmethod
    def from_env(cls):
        result = cls(socket_path=os.environ['ACCESS_BROKER_SOCKET'], token_file=os.environ['ACCESS_BROKER_TOKEN_FILE'],
                     runtime_id=os.environ['ACCESS_RUNTIME_ID'], host=os.getenv('ACCESS_HOST', '127.0.0.1'),
                     port=int(os.getenv('ACCESS_PORT', '9000')), health_port=int(os.getenv('ACCESS_HEALTH_PORT', '9002')),
                     capacity=int(os.getenv('ACCESS_CAPACITY', '1')),
                     ssh_capacity=int(os.getenv('ACCESS_SSH_CAPACITY', '8')))
        result.validate()
        return result
