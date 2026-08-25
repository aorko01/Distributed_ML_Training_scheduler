import io
import os
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from minio import Minio

from init_buckets import ensure_buckets

MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "minio:9000")
MINIO_ROOT_USER = os.environ.get("MINIO_ROOT_USER", "minioadmin")
MINIO_ROOT_PASSWORD = os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin")
MINIO_SECURE = os.environ.get("MINIO_SECURE", "false").lower() == "true"
# Endpoint used to build presigned URLs. Must be reachable by clients (workers),
# so it cannot be the Docker-internal "minio:9000".
MINIO_PUBLIC_ENDPOINT = os.environ.get("MINIO_PUBLIC_ENDPOINT", "localhost:9000")
# TLS of the public endpoint (nginx terminates HTTPS in front of MinIO).
MINIO_PUBLIC_SECURE = (
    os.environ.get("MINIO_PUBLIC_SECURE", str(MINIO_SECURE)).lower() == "true"
)


def get_client() -> Minio:
    return Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ROOT_USER,
        secret_key=MINIO_ROOT_PASSWORD,
        secure=MINIO_SECURE,
    )


def get_public_client() -> Minio:
    return Minio(
        MINIO_PUBLIC_ENDPOINT,
        access_key=MINIO_ROOT_USER,
        secret_key=MINIO_ROOT_PASSWORD,
        secure=MINIO_PUBLIC_SECURE,
        region="us-east-1",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_buckets(get_client())
    yield


app = FastAPI(title="Object Store", version="1.0.0", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/objects/upload")
async def upload_object(
    bucket: str = Form(...),
    object_key: str = Form(...),
    file: UploadFile = File(...),
):
    client = get_client()

    if not client.bucket_exists(bucket):
        raise HTTPException(status_code=404, detail=f"Bucket '{bucket}' not found")

    contents = await file.read()
    client.put_object(
        bucket_name=bucket,
        object_name=object_key,
        data=io.BytesIO(contents),
        length=len(contents),
        content_type=file.content_type or "application/octet-stream",
    )

    return {
        "bucket": bucket,
        "object_key": object_key,
        "size": len(contents),
    }


@app.post("/objects/presign_upload")
def presign_upload(
    bucket: str = Form(...),
    object_key: str = Form(...),
    expires: int = Form(3600),
):
    client = get_client()

    if not client.bucket_exists(bucket):
        raise HTTPException(status_code=404, detail=f"Bucket '{bucket}' not found")

    try:
        url = get_public_client().presigned_put_object(
            bucket, object_key, expires=timedelta(seconds=expires)
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"url": url, "bucket": bucket, "object_key": object_key}


@app.get("/objects/list")
def list_objects(bucket: str, prefix: str = ""):
    """List objects in a bucket, optionally filtered by a key prefix.

    Used by workers to discover a job's previously uploaded output files
    (e.g. checkpoints) so a retry can restore them before resuming training.
    """
    client = get_client()

    if not client.bucket_exists(bucket):
        raise HTTPException(status_code=404, detail=f"Bucket '{bucket}' not found")

    objects = []
    try:
        for obj in client.list_objects(bucket, prefix=prefix, recursive=True):
            objects.append(
                {
                    "key": obj.object_name,
                    "size": obj.size,
                    "last_modified": (
                        obj.last_modified.isoformat() if obj.last_modified else None
                    ),
                }
            )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"bucket": bucket, "prefix": prefix, "objects": objects}


@app.post("/objects/presign_download")
def presign_download(
    bucket: str = Form(...),
    object_key: str = Form(...),
    expires: int = Form(3600),
):
    """Presigned GET URL so large outputs can be downloaded straight from MinIO,
    bypassing the proxied endpoint that rejects large payloads."""
    client = get_client()

    if not client.bucket_exists(bucket):
        raise HTTPException(status_code=404, detail=f"Bucket '{bucket}' not found")

    try:
        url = get_public_client().presigned_get_object(
            bucket, object_key, expires=timedelta(seconds=expires)
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"url": url, "bucket": bucket, "object_key": object_key}


@app.get("/objects/{bucket}/{object_key:path}")
def download_object(bucket: str, object_key: str):
    client = get_client()

    if not client.bucket_exists(bucket):
        raise HTTPException(status_code=404, detail=f"Bucket '{bucket}' not found")

    try:
        response = client.get_object(bucket, object_key)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    def iter_response(resp):
        try:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                yield chunk
        finally:
            resp.close()
            resp.release_conn()

    return StreamingResponse(
        iter_response(response),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{object_key.split("/")[-1]}"'
        },
    )
