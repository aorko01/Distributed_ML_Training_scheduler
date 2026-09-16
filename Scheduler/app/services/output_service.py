"""Build an authenticated, per-job output archive from object-store files.

The archive is assembled on disk and every object is streamed into it.  This
keeps Scheduler memory bounded for multi-gigabyte checkpoints and ensures an
archive is returned only when every advertised entry was downloaded.
"""

import os
import tempfile
import zipfile
from urllib.parse import quote

import requests


OBJECT_STORE_URL = os.environ.get(
    "OBJECT_STORE_URL", "http://localhost:8010"
).rstrip("/")
OBJECT_STORE_BUCKET = os.environ.get("OBJECT_STORE_BUCKET", "uploads")
OBJECT_OUTPUT_BUCKET = os.environ.get("OBJECT_OUTPUT_BUCKET", "outputs")
OBJECT_STORE_LARGE_FILE_THRESHOLD = int(
    os.environ.get("OBJECT_STORE_LARGE_FILE_THRESHOLD", str(50 * 1024 * 1024))
)

_LIST_TIMEOUT = 30
_PRESIGN_TIMEOUT = 30
_DOWNLOAD_TIMEOUT = (10, 3600)
_CHUNK_SIZE = 1 << 20


def is_safe_job_id(job_id: str) -> bool:
    """Reject job ids that could escape the ``<job_id>/`` prefix."""
    if not job_id or not isinstance(job_id, str):
        return False
    if job_id in (".", ".."):
        return False
    if "/" in job_id or "\\" in job_id or ".." in job_id:
        return False
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in job_id):
        return False
    return True


def safe_output_rel_path(job_id: str, key: str) -> str | None:
    """Strip ``<job_id>/`` while rejecting unsafe ZIP entry paths."""
    if not isinstance(key, str):
        return None
    prefix = f"{job_id}/"
    if not key.startswith(prefix):
        return None
    rel = key[len(prefix):]
    if not rel or rel.startswith("/") or "\\" in rel or "//" in rel:
        return None
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in rel):
        return None
    parts = rel.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    return rel


def list_bucket_objects(bucket: str, prefix: str) -> list[dict]:
    """List objects in ``bucket`` under ``prefix`` via the Object Store API."""
    try:
        response = requests.get(
            f"{OBJECT_STORE_URL}/objects/list",
            params={"bucket": bucket, "prefix": prefix},
            timeout=_LIST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        raise RuntimeError(f"Object store list failed: {exc}") from exc

    objects = payload.get("objects") if isinstance(payload, dict) else None
    if not isinstance(objects, list):
        raise RuntimeError("Object store list returned an invalid objects payload")
    return objects


def get_presigned_download_url(bucket: str, object_key: str) -> str:
    """Return a direct object URL that bypasses response-size-limited proxies."""
    try:
        response = requests.post(
            f"{OBJECT_STORE_URL}/objects/presign_download",
            data={"bucket": bucket, "object_key": object_key},
            timeout=_PRESIGN_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        url = payload.get("url") if isinstance(payload, dict) else None
        if not isinstance(url, str) or not url:
            raise ValueError("missing URL")
        return url
    except Exception as exc:
        raise RuntimeError(
            f"Could not create download URL for {object_key!r}: {exc}"
        ) from exc


def _proxied_download_url(bucket: str, object_key: str) -> str:
    safe_bucket = quote(bucket, safe="")
    safe_key = quote(object_key, safe="/")
    return f"{OBJECT_STORE_URL}/objects/{safe_bucket}/{safe_key}"


def stream_object_to_zip(
    archive: zipfile.ZipFile,
    *,
    bucket: str,
    object_key: str,
    archive_name: str,
    size: int | None = None,
    force_presigned: bool = False,
) -> None:
    """Download one object in chunks directly into an open ZIP entry."""
    use_presigned = force_presigned or (
        size is not None and size >= OBJECT_STORE_LARGE_FILE_THRESHOLD
    )
    urls: list[str] = []
    last_error: Exception | None = None
    if use_presigned:
        try:
            urls.append(get_presigned_download_url(bucket, object_key))
        except RuntimeError as exc:
            # A bad public MinIO endpoint must not break downloads when the
            # Object Store's streaming proxy is still reachable.
            last_error = exc
    proxy_url = _proxied_download_url(bucket, object_key)
    if not urls or urls[-1] != proxy_url:
        urls.append(proxy_url)

    response = None
    for url in urls:
        candidate = None
        try:
            candidate = requests.get(url, stream=True, timeout=_DOWNLOAD_TIMEOUT)
            candidate.raise_for_status()
            response = candidate
            break
        except Exception as exc:
            last_error = exc
            if candidate is not None:
                candidate.close()

    if response is None:
        raise RuntimeError(
            f"Failed to download object {bucket}/{object_key}: {last_error}"
        ) from last_error

    try:
        with archive.open(archive_name, "w", force_zip64=True) as destination:
            for chunk in response.iter_content(_CHUNK_SIZE):
                if chunk:
                    destination.write(chunk)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to download object {bucket}/{object_key}: {exc}"
        ) from exc
    finally:
        response.close()


def cleanup_archive(path: str) -> None:
    """Best-effort removal used after responses and failed archive builds."""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def build_job_output_zip(job_id: str, submitted_object_key: str | None = None) -> str:
    """Build a complete job ZIP on disk and return its temporary path.

    The archive contains ``submitted/<upload-name>`` and every safe object
    listed below ``<job_id>/`` in the output bucket. Any expected-object
    download failure aborts and removes the archive rather than returning a
    misleading partial success.
    """
    if not is_safe_job_id(job_id):
        raise ValueError(f"Invalid job_id {job_id!r}")

    entries: list[tuple[str, str, str, int | None, bool]] = []
    archive_names: set[str] = set()

    if submitted_object_key:
        submitted_rel = safe_output_rel_path(job_id, submitted_object_key)
        if submitted_rel is None:
            raise ValueError("Submitted object key does not belong to this job")
        submitted_name = os.path.basename(submitted_rel)
        archive_name = f"submitted/{submitted_name}"
        entries.append(
            (OBJECT_STORE_BUCKET, submitted_object_key, archive_name, None, True)
        )
        archive_names.add(archive_name)

    for obj in list_bucket_objects(OBJECT_OUTPUT_BUCKET, f"{job_id}/"):
        if not isinstance(obj, dict):
            continue
        key = obj.get("key")
        rel = safe_output_rel_path(job_id, key)
        if rel is None:
            continue
        archive_name = f"outputs/{rel}"
        if archive_name in archive_names:
            raise RuntimeError(f"Duplicate archive entry {archive_name!r}")
        archive_names.add(archive_name)
        raw_size = obj.get("size")
        try:
            size = int(raw_size) if raw_size is not None else None
        except (TypeError, ValueError):
            size = None
        entries.append((OBJECT_OUTPUT_BUCKET, key, archive_name, size, False))

    if not entries:
        raise FileNotFoundError(f"No output files found for job_id {job_id}")

    try:
        file_descriptor, archive_path = tempfile.mkstemp(
            prefix=f"job-output-{job_id}-", suffix=".zip"
        )
    except OSError as exc:
        raise RuntimeError(f"Could not create output archive: {exc}") from exc
    os.close(file_descriptor)
    try:
        with zipfile.ZipFile(
            archive_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
        ) as archive:
            for bucket, key, archive_name, size, force_presigned in entries:
                stream_object_to_zip(
                    archive,
                    bucket=bucket,
                    object_key=key,
                    archive_name=archive_name,
                    size=size,
                    force_presigned=force_presigned,
                )
        return archive_path
    except (FileNotFoundError, RuntimeError, ValueError):
        cleanup_archive(archive_path)
        raise
    except Exception as exc:
        cleanup_archive(archive_path)
        raise RuntimeError(f"Could not build output archive: {exc}") from exc
