"""Private, bounded transport for immutable workspace snapshot archives.

The Scheduler alone can mint capabilities and check receipts. Workers and
Builders receive an operation-scoped token, never MinIO credentials.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import stat
import tempfile
import time

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from minio import Minio
from minio.error import S3Error
from pydantic import BaseModel, ConfigDict, Field


router = APIRouter(prefix="/objects/internal/snapshots", tags=["private snapshots"])
SNAPSHOT_BUCKET = os.getenv("SNAPSHOT_BUCKET", "snapshots")
MAX_SIZE = 8 * 1024 * 1024 * 1024
KEY = re.compile(r"^snapshots/[0-9a-f-]{36}/[0-9a-f-]{36}/[0-9a-f-]{36}/[0-9a-f]{64}\.tar\.gz$")
OP = re.compile(r"^[0-9a-f-]{36}$")


class Descriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation_id: str
    object_key: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(gt=0, le=MAX_SIZE)


def _secret():
    path = os.environ.get("SNAPSHOT_SERVICE_SECRET_FILE")
    if not path:
        raise HTTPException(503, "Snapshot service unavailable")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or not 32 <= info.st_size <= 256:
                raise ValueError()
            value = os.read(fd, 257).strip()
        finally:
            os.close(fd)
        if len(value) < 32:
            raise ValueError()
        return value
    except (OSError, ValueError):
        raise HTTPException(503, "Snapshot service unavailable") from None


def _admin(authorization):
    if not hmac.compare_digest(authorization.encode(), b"Bearer " + _secret()):
        raise HTTPException(401, "Snapshot service authentication required")


def _valid(body):
    if not OP.fullmatch(body.operation_id) or not KEY.fullmatch(body.object_key):
        raise HTTPException(422, "Invalid snapshot identity")
    parts = body.object_key.split("/")
    if parts[3] != body.operation_id or parts[4] != body.sha256 + ".tar.gz":
        raise HTTPException(422, "Snapshot identity mismatch")


def _token(body, purpose):
    payload = {**body.model_dump(), "purpose": purpose, "expires": int(time.time()) + 3600}
    raw = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).rstrip(b"=")
    sig = hmac.new(_secret(), raw, hashlib.sha256).hexdigest().encode()
    return (raw + b"." + sig).decode()


def _capability(authorization, operation_id, purpose):
    if not authorization.startswith("Snapshot "):
        raise HTTPException(401, "Snapshot capability required")
    try:
        raw, signature = authorization[9:].encode().split(b".", 1)
        if len(raw) > 2048 or len(signature) != 64:
            raise ValueError()
        expect = hmac.new(_secret(), raw, hashlib.sha256).hexdigest().encode()
        if not hmac.compare_digest(expect, signature):
            raise ValueError()
        data = json.loads(base64.urlsafe_b64decode(raw + b"=" * (-len(raw) % 4)))
        body = Descriptor(**{k: data[k] for k in ("operation_id", "object_key", "sha256", "size")})
        _valid(body)
        if data["purpose"] != purpose or data["expires"] < time.time() or body.operation_id != operation_id:
            raise ValueError()
        return body
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise HTTPException(401, "Invalid snapshot capability") from None


def _client():
    return Minio(
        os.getenv("MINIO_ENDPOINT", "minio:9000"),
        access_key=os.getenv("MINIO_ROOT_USER", "minioadmin"),
        secret_key=os.getenv("MINIO_ROOT_PASSWORD", "minioadmin"),
        secure=os.getenv("MINIO_SECURE", "false").lower() == "true",
    )


def _stat(client, body):
    try:
        item = client.stat_object(SNAPSHOT_BUCKET, body.object_key)
    except S3Error as exc:
        if exc.code in ("NoSuchKey", "NoSuchObject", "NotFound"):
            return None
        raise HTTPException(503, "Snapshot storage unavailable") from None
    metadata = {str(key).lower(): value for key, value in (item.metadata or {}).items()}
    if item.size != body.size or metadata.get("x-amz-meta-sha256") != body.sha256:
        raise HTTPException(409, "Snapshot object conflict")
    return item


def _receipt(body, item):
    return {"operation_id": body.operation_id, "sha256": body.sha256,
            "size": body.size, "storage_version": body.operation_id + ":" + item.etag}


@router.post("/capability")
def capability(body: Descriptor, authorization: str = Header(default="")):
    _admin(authorization)
    _valid(body)
    return {"upload_url": f"/objects/internal/snapshots/{body.operation_id}",
            "upload_token": _token(body, "put"), "expires_seconds": 3600}


@router.put("/{operation_id}")
async def upload(operation_id: str, request: Request, authorization: str = Header(default="")):
    body = _capability(authorization, operation_id, "put")
    client = _client()
    existing = _stat(client, body)
    if existing:
        return _receipt(body, existing)
    sha = hashlib.sha256()
    size = 0
    # Spooled to disk with a fixed upper bound; FastAPI never buffers the
    # multi-gigabyte request in memory. Upload directly from this file to S3.
    with tempfile.TemporaryFile(mode="w+b") as stream:
        async for chunk in request.stream():
            size += len(chunk)
            if size > body.size or size > MAX_SIZE:
                raise HTTPException(413, "Snapshot exceeds expected size")
            sha.update(chunk)
            stream.write(chunk)
        if size != body.size or sha.hexdigest() != body.sha256:
            raise HTTPException(422, "Snapshot checksum mismatch")
        stream.seek(0)
        # A retry sees the same content. The dedicated bucket is unavailable
        # through the generic routes; only this exact capability can write it.
        existing = _stat(client, body)
        if existing:
            return _receipt(body, existing)
        try:
            client.put_object(SNAPSHOT_BUCKET, body.object_key, stream, length=size,
                              part_size=64 * 1024 * 1024,
                              metadata={"sha256": body.sha256},
                              content_type="application/gzip")
        except Exception:
            raise HTTPException(503, "Snapshot storage unavailable") from None
    item = _stat(client, body)
    if item is None:
        raise HTTPException(503, "Snapshot receipt unavailable")
    return _receipt(body, item)


@router.post("/receipt")
def receipt(body: Descriptor, authorization: str = Header(default="")):
    _admin(authorization)
    _valid(body)
    item = _stat(_client(), body)
    if item is None:
        raise HTTPException(404, "Snapshot not uploaded")
    return _receipt(body, item)


@router.post("/download-capability")
def download_capability(body: Descriptor, authorization: str = Header(default="")):
    _admin(authorization)
    _valid(body)
    if _stat(_client(), body) is None:
        raise HTTPException(404, "Snapshot not uploaded")
    return {"download_url": f"/objects/internal/snapshots/{body.operation_id}",
            "download_token": _token(body, "get"), "expires_seconds": 3600}


@router.get("/{operation_id}")
def download(operation_id: str, authorization: str = Header(default="")):
    body = _capability(authorization, operation_id, "get")
    client = _client()
    if _stat(client, body) is None:
        raise HTTPException(404, "Snapshot not uploaded")
    try:
        response = client.get_object(SNAPSHOT_BUCKET, body.object_key)
    except Exception:
        raise HTTPException(503, "Snapshot storage unavailable") from None

    def chunks():
        try:
            while data := response.read(1 << 20):
                yield data
        finally:
            response.close()
            response.release_conn()

    return StreamingResponse(chunks(), media_type="application/gzip",
                             headers={"Content-Length": str(body.size)})
