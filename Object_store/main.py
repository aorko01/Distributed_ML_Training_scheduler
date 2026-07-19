import io
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from minio import Minio

from init_buckets import ensure_buckets

MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "minio:9000")
MINIO_ROOT_USER = os.environ.get("MINIO_ROOT_USER", "minioadmin")
MINIO_ROOT_PASSWORD = os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin")
MINIO_SECURE = os.environ.get("MINIO_SECURE", "false").lower() == "true"


def get_client() -> Minio:
    return Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ROOT_USER,
        secret_key=MINIO_ROOT_PASSWORD,
        secure=MINIO_SECURE,
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


@app.get("/objects/{bucket}/{object_key:path}")
def download_object(bucket: str, object_key: str):
    client = get_client()

    if not client.bucket_exists(bucket):
        raise HTTPException(status_code=404, detail=f"Bucket '{bucket}' not found")

    try:
        response = client.get_object(bucket, object_key)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return StreamingResponse(
        response,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{object_key.split("/")[-1]}"'
        },
    )
