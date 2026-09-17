"""Validate untrusted ZIP metadata before storage; builder validates again."""
import io
import stat
import zipfile
from pathlib import PurePosixPath

MAX_UPLOAD = 64 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024
MAX_FILES = 10000


def validate_archive(data):
    if len(data) > MAX_UPLOAD:
        raise ValueError('Archive exceeds 64 MiB')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_FILES or sum(x.file_size for x in entries) > MAX_EXPANDED:
                raise ValueError('Expanded archive limit exceeded')
            names = set()
            required = False
            for entry in entries:
                path = PurePosixPath(entry.filename)
                mode = entry.external_attr >> 16
                if not entry.filename or path.is_absolute() or '..' in path.parts or '\\' in entry.filename or ':' in entry.filename or '\x00' in entry.filename:
                    raise ValueError('Unsafe archive path')
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))):
                    raise ValueError('Archive contains unsupported file type')
                if str(path) in names:
                    raise ValueError('Duplicate archive path')
                names.add(str(path))
                if path.name in ('Dockerfile', '.dockerignore'):
                    raise ValueError('Submitted Docker build instructions are not allowed')
                required |= path.name == 'requirements.txt' and not entry.is_dir()
            if not required:
                raise ValueError('requirements.txt is required')
    except zipfile.BadZipFile:
        raise ValueError('Invalid ZIP archive') from None
