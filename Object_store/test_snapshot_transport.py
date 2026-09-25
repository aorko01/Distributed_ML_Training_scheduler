"""A scoped capability can move bytes; generic snapshot access stays closed."""
import hashlib
import io
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from minio.error import S3Error

import snapshot_transport as snapshots
from main import ordinary_bucket


ID = "12345678-1234-5678-1234-567812345678"


class Store:
    def __init__(self):
        self.items = {}

    def stat_object(self, bucket, key):
        if key not in self.items:
            raise S3Error(None, "NoSuchKey", "missing", "", "", "")
        content, metadata = self.items[key]
        return SimpleNamespace(size=len(content), metadata=metadata, etag=hashlib.md5(content).hexdigest())

    def put_object(self, bucket, key, stream, length, **kwargs):
        content = stream.read(length)
        self.items[key] = (content, {"x-amz-meta-sha256": kwargs["metadata"]["sha256"]})

    def get_object(self, bucket, key):
        content, _ = self.items[key]
        stream = io.BytesIO(content)
        stream.release_conn = lambda: None
        return stream


def test_snapshot_upload_receipt_and_download(monkeypatch, tmp_path):
    secret = tmp_path / "snapshot.key"
    secret.write_bytes(b"a-strong-dedicated-snapshot-service-secret-1234")
    secret.chmod(0o600)
    monkeypatch.setenv("SNAPSHOT_SERVICE_SECRET_FILE", str(secret))
    store = Store()
    monkeypatch.setattr(snapshots, "_client", lambda: store)
    app = FastAPI()
    app.include_router(snapshots.router)
    client = TestClient(app)
    data = b"gzip archive bytes" * 100
    digest = hashlib.sha256(data).hexdigest()
    body = {"operation_id": ID, "object_key": f"snapshots/{ID}/{ID}/{ID}/{digest}.tar.gz",
            "sha256": digest, "size": len(data)}
    admin = {"Authorization": "Bearer " + secret.read_text()}
    cap = client.post("/objects/internal/snapshots/capability", json=body, headers=admin).json()
    upload = client.put(cap["upload_url"], content=data,
                        headers={"Authorization": "Snapshot " + cap["upload_token"]})
    assert upload.status_code == 200
    receipt = client.post("/objects/internal/snapshots/receipt", json=body, headers=admin)
    assert receipt.status_code == 200
    assert receipt.json()["storage_version"] == upload.json()["storage_version"]
    download = client.post("/objects/internal/snapshots/download-capability", json=body, headers=admin).json()
    fetched = client.get(download["download_url"], headers={"Authorization": "Snapshot " + download["download_token"]})
    assert fetched.content == data
    assert client.get(download["download_url"]).status_code == 401
    bad = client.put(cap["upload_url"], content=data + b"tampered",
                     headers={"Authorization": "Snapshot " + cap["upload_token"]})
    assert bad.status_code == 200  # same immutable object; no overwrite occurs
    assert store.items[body["object_key"]][0] == data


def test_generic_snapshot_bucket_is_denied():
    from fastapi import HTTPException
    try:
        ordinary_bucket(snapshots.SNAPSHOT_BUCKET)
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("generic snapshot bucket was exposed")
