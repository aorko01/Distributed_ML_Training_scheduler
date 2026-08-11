import io
import os
import logging

import requests

from config import OBJECT_STORE_URL, OBJECT_OUTPUT_BUCKET

logger = logging.getLogger("object_store")


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
        try:
            resp = requests.post(
                f"{self.base_url}/objects/upload",
                data={"bucket": self.bucket, "object_key": object_key},
                files={
                    "file": (
                        os.path.basename(object_key),
                        io.BytesIO(content),
                        content_type,
                    )
                },
                timeout=60,
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.warning("Failed to upload %s: %s", object_key, e)
            return False

    def upload_file(self, object_key: str, file_path: str) -> bool:
        try:
            with open(file_path, "rb") as f:
                resp = requests.post(
                    f"{self.base_url}/objects/upload",
                    data={"bucket": self.bucket, "object_key": object_key},
                    files={"file": (os.path.basename(file_path), f)},
                    timeout=60,
                )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.warning("Failed to upload file %s: %s", file_path, e)
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
