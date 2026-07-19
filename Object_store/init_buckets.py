import os

from minio import Minio
from minio.error import S3Error

MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "localhost:9000")
MINIO_ROOT_USER = os.environ.get("MINIO_ROOT_USER", "minioadmin")
MINIO_ROOT_PASSWORD = os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin")
MINIO_SECURE = os.environ.get("MINIO_SECURE", "false").lower() == "true"

BUCKETS = (
    os.environ.get("OBJECT_STORE_BUCKET", "uploads"),
    os.environ.get("OBJECT_OUTPUT_BUCKET", "outputs"),
)


def get_client() -> Minio:
    return Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ROOT_USER,
        secret_key=MINIO_ROOT_PASSWORD,
        secure=MINIO_SECURE,
    )


def ensure_buckets(client: Minio | None = None) -> None:
    storage_client = client or get_client()

    for bucket_name in BUCKETS:
        if not bucket_name:
            continue

        try:
            if not storage_client.bucket_exists(bucket_name):
                storage_client.make_bucket(bucket_name)
        except S3Error as exc:
            raise RuntimeError(
                f"Could not ensure bucket '{bucket_name}': {exc}"
            ) from exc


if __name__ == "__main__":
    ensure_buckets()
