import io
import os
import time
import logging

import requests

from config import (
    OBJECT_STORE_URL,
    OBJECT_OUTPUT_BUCKET,
    OBJECT_STORE_LARGE_FILE_THRESHOLD,
)

logger = logging.getLogger("object_store")


def _post_retry(
    url: str,
    *,
    data: dict,
    files: dict,
    timeout: float = 600,
    retries: int = 5,
) -> bool:
    """POST to the object store with exponential backoff on transient errors."""
    last_err = None
    for attempt in range(retries):
        try:
            resp = requests.post(url, data=data, files=files, timeout=timeout)
            resp.raise_for_status()
            return True
        except Exception as e:
            last_err = e
            logger.warning(
                "Object store POST failed (attempt %d/%d): %s",
                attempt + 1, retries, e,
            )
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    logger.error("Object store upload failed after %d attempts: %s", retries, last_err)
    return False


class ObjectStore:
    def __init__(self, base_url: str | None = None, bucket: str | None = None):
        self.base_url = (base_url or OBJECT_STORE_URL).rstrip("/")
        self.bucket = bucket or OBJECT_OUTPUT_BUCKET

    def upload_bytes(
        self,
        object_key: str,
        content: bytes,
        content_type: str = "application/octet-stream",
    ) -> bool:
        return _post_retry(
            f"{self.base_url}/objects/upload",
            data={"bucket": self.bucket, "object_key": object_key},
            files={
                "file": (
                    os.path.basename(object_key),
                    io.BytesIO(content),
                    content_type,
                )
            },
        )

    def upload_file(self, object_key: str, file_path: str) -> bool:
        try:
            size = os.path.getsize(file_path)
        except OSError as e:
            logger.warning("Failed to stat file %s for upload: %s", file_path, e)
            return False

        if size >= OBJECT_STORE_LARGE_FILE_THRESHOLD:
            return self._upload_large(object_key, file_path)

        try:
            with open(file_path, "rb") as f:
                return _post_retry(
                    f"{self.base_url}/objects/upload",
                    data={"bucket": self.bucket, "object_key": object_key},
                    files={"file": (os.path.basename(file_path), f)},
                )
        except OSError as e:
            logger.warning("Failed to read file %s for upload: %s", file_path, e)
            return False

    def _presign_upload(self, object_key: str) -> str:
        resp = requests.post(
            f"{self.base_url}/objects/presign_upload",
            data={"bucket": self.bucket, "object_key": object_key},
            timeout=30,
        )
        resp.raise_for_status()
        url = resp.json().get("url")
        if not url:
            raise ValueError(f"No presigned URL returned for {object_key}")
        return url

    def _upload_large(self, object_key: str, file_path: str) -> bool:
        """Upload big files straight to MinIO via a presigned URL, bypassing the
        proxied endpoint (the proxy rejects payloads over ~100MB)."""
        retries = 5
        last_err = None
        for attempt in range(retries):
            try:
                url = self._presign_upload(object_key)
                with open(file_path, "rb") as f:
                    resp = requests.put(url, data=f, timeout=3600)
                resp.raise_for_status()
                return True
            except Exception as e:
                last_err = e
                logger.warning(
                    "Large object store upload failed (attempt %d/%d) for %s: %s",
                    attempt + 1, retries, object_key, e,
                )
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
        logger.error(
            "Large object store upload failed after %d attempts for %s: %s",
            retries, object_key, last_err,
        )
        return False

    def download(self, object_key: str) -> bytes | None:
        try:
            resp = requests.get(
                f"{self.base_url}/objects/{self.bucket}/{object_key}", timeout=30
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.content
        except Exception as e:
            logger.warning("Failed to download %s: %s", object_key, e)
            return None
