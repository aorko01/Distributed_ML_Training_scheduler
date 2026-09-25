"""Build an authenticated, per-job output archive from object-store files.

The archive is assembled on disk and every object is streamed into it.  This
keeps Scheduler memory bounded for multi-gigabyte checkpoints and ensures an
archive is returned only when every advertised entry was downloaded.
"""

import binascii
import logging
import os
import struct
import tempfile
import time
import zipfile
from collections.abc import AsyncIterator
from urllib.parse import quote

import httpx
import requests

logger = logging.getLogger(__name__)


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


OutputEntry = tuple[str, str, str, int | None, bool]


def collect_job_output_entries(
    job_id: str, submitted_object_key: str | None = None
) -> list[OutputEntry]:
    """List the ``(bucket, key, archive_name, size, force_presigned)`` entries
    for a job's output archive without downloading anything.

    Shared by the legacy on-disk builder and the streaming responder so both
    advertise exactly the same entries. Raises ``ValueError`` for unsafe ids,
    ``RuntimeError`` on duplicate archive names.
    """
    if not is_safe_job_id(job_id):
        raise ValueError(f"Invalid job_id {job_id!r}")

    entries: list[OutputEntry] = []
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

    return entries


def build_job_output_zip(job_id: str, submitted_object_key: str | None = None) -> str:
    """Build a complete job ZIP on disk and return its temporary path.

    The archive contains ``submitted/<upload-name>`` and every safe object
    listed below ``<job_id>/`` in the output bucket. Any expected-object
    download failure aborts and removes the archive rather than returning a
    misleading partial success.
    """
    entries = collect_job_output_entries(job_id, submitted_object_key)

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


# ---------------------------------------------------------------------------
# Streaming ZIP responder.
#
# The on-disk builder above must download every object before the HTTP
# response sends its first byte, so large checkpoints leave the browser stuck
# on "Preparing…" until proxies/timeouts give up. The streamer below emits a
# stored (uncompressed) ZIP64 archive with data descriptors, which lets the
# server forward each object's bytes to the client as they arrive: the first
# archive byte is sent right after the (fast) object listing, the browser
# download manager shows live progress, and neither server disk nor server
# RAM holds the whole archive. Stored is deliberate: checkpoints/weights are
# already compressed binaries, so DEFLATE would only burn CPU and delay the
# first byte without shrinking the download.
# ---------------------------------------------------------------------------

_ZIP_LOCAL_SIG = 0x04034B50
_ZIP_DATA_DESCRIPTOR_SIG = 0x08074B50
_ZIP_CENTRAL_SIG = 0x02014B50
_ZIP64_EOCD_SIG = 0x06064B50
_ZIP64_LOCATOR_SIG = 0x07064B50
_ZIP_EOCD_SIG = 0x06054B50
_ZIP64_PLACEHOLDER_32 = 0xFFFFFFFF
_ZIP64_PLACEHOLDER_16 = 0xFFFF
_ZIP_STORED_METHOD = 0
# Bit 3: CRC/sizes follow in a data descriptor (unknown while streaming).
# Bit 11: filename is UTF-8.
_ZIP_STREAM_FLAGS = 0x08 | 0x800
_ZIP_VERSION_ZIP64 = 45

_STREAM_READ_TIMEOUT = httpx.Timeout(connect=10.0, read=3600.0, write=10.0, pool=10.0)


def _should_try_presigned_first(size: int | None, force_presigned: bool) -> bool:
    return force_presigned or (
        size is not None and size >= OBJECT_STORE_LARGE_FILE_THRESHOLD
    )


def _dos_time_date(timestamp: float | None = None) -> tuple[int, int]:
    moment = time.localtime(timestamp)
    dos_time = (
        ((moment.tm_hour & 0x1F) << 11)
        | ((moment.tm_min & 0x3F) << 5)
        | ((moment.tm_sec // 2) & 0x1F)
    )
    dos_date = (
        (((moment.tm_year - 1980) & 0x7F) << 9)
        | ((moment.tm_mon & 0xF) << 5)
        | (moment.tm_mday & 0x1F)
    )
    return dos_time, dos_date


def _zip64_local_header(name: bytes, dos_time: int, dos_date: int) -> bytes:
    # ZIP64 extra for the local header carries zeroed placeholder sizes; the
    # real CRC/sizes travel in the data descriptor after the file data.
    extra = struct.pack("<HHQQ", 0x0001, 16, 0, 0)
    return (
        struct.pack(
            "<IHHHHHIIIHH",
            _ZIP_LOCAL_SIG,
            _ZIP_VERSION_ZIP64,
            _ZIP_STREAM_FLAGS,
            _ZIP_STORED_METHOD,
            dos_time,
            dos_date,
            0,  # crc32 (see data descriptor)
            _ZIP64_PLACEHOLDER_32,  # compressed size
            _ZIP64_PLACEHOLDER_32,  # uncompressed size
            len(name),
            len(extra),
        )
        + name
        + extra
    )


def _zip64_data_descriptor(crc: int, size: int) -> bytes:
    return struct.pack(
        "<IIQQ", _ZIP_DATA_DESCRIPTOR_SIG, crc & 0xFFFFFFFF, size, size
    )


def _zip64_central_entry(
    name: bytes,
    crc: int,
    size: int,
    header_offset: int,
    dos_time: int,
    dos_date: int,
) -> bytes:
    extra = struct.pack(
        "<HHQQQ", 0x0001, 24, size, size, header_offset
    )
    external_attr = (0o600 << 16)  # regular file, rw-------
    return (
        struct.pack(
            "<IHHHHHHIIIHHHHHII",
            _ZIP_CENTRAL_SIG,
            (3 << 8) | _ZIP_VERSION_ZIP64,  # version made by (Unix, 4.5)
            _ZIP_VERSION_ZIP64,  # version needed
            _ZIP_STREAM_FLAGS,
            _ZIP_STORED_METHOD,
            dos_time,
            dos_date,
            crc & 0xFFFFFFFF,
            _ZIP64_PLACEHOLDER_32,
            _ZIP64_PLACEHOLDER_32,
            len(name),
            len(extra),
            0,  # comment length
            0,  # disk number
            0,  # internal attrs
            external_attr,
            _ZIP64_PLACEHOLDER_32,  # local header offset (real one in extra)
        )
        + name
        + extra
    )


def _zip64_end_records(
    central_count: int, central_size: int, central_offset: int
) -> bytes:
    zip64_eocd_offset = central_offset + central_size
    zip64_eocd = struct.pack(
        "<IQHHIIQQQQ",
        _ZIP64_EOCD_SIG,
        44,  # size of the remaining record
        (3 << 8) | _ZIP_VERSION_ZIP64,
        _ZIP_VERSION_ZIP64,
        0,  # this disk
        0,  # central-dir disk
        central_count,
        central_count,
        central_size,
        central_offset,
    )
    locator = struct.pack(
        "<IIQI", _ZIP64_LOCATOR_SIG, 0, zip64_eocd_offset, 1
    )
    eocd = struct.pack(
        "<IHHHHIIH",
        _ZIP_EOCD_SIG,
        0,  # this disk
        0,  # central-dir disk
        min(central_count, _ZIP64_PLACEHOLDER_16),
        min(central_count, _ZIP64_PLACEHOLDER_16),
        _ZIP64_PLACEHOLDER_32,  # central size (see ZIP64 record)
        _ZIP64_PLACEHOLDER_32,  # central offset (see ZIP64 record)
        0,  # comment length
    )
    return zip64_eocd + locator + eocd


async def _apresign_download_url(
    client: httpx.AsyncClient, bucket: str, object_key: str
) -> str:
    try:
        response = await client.post(
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


async def _afetch_object_chunks(
    client: httpx.AsyncClient,
    *,
    bucket: str,
    object_key: str,
    size: int | None,
    force_presigned: bool,
) -> AsyncIterator[bytes]:
    """Yield an object's raw bytes, trying a presigned URL before the proxy.

    Separated out so unit tests can patch a single seam to feed fake bytes.
    """
    urls: list[str] = []
    last_error: Exception | None = None
    if _should_try_presigned_first(size, force_presigned):
        try:
            urls.append(await _apresign_download_url(client, bucket, object_key))
        except RuntimeError as exc:
            # A bad public MinIO endpoint must not break downloads when the
            # Object Store's streaming proxy is still reachable.
            last_error = exc
    proxy_url = _proxied_download_url(bucket, object_key)
    if not urls or urls[-1] != proxy_url:
        urls.append(proxy_url)

    for url in urls:
        try:
            async with client.stream(
                "GET", url, timeout=_STREAM_READ_TIMEOUT, follow_redirects=True
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes(_CHUNK_SIZE):
                    if chunk:
                        yield chunk
                return
        except Exception as exc:
            last_error = exc
            continue
    raise RuntimeError(
        f"Failed to download object {bucket}/{object_key}: {last_error}"
    ) from last_error


async def aiter_entries_as_zip(entries: list[OutputEntry]) -> AsyncIterator[bytes]:
    """Stream ``entries`` as a stored ZIP64 archive, chunk by chunk.

    The listing must already be collected (fast) so the first archive byte
    goes out immediately and the client sees download progress while large
    objects are still being fetched. A mid-stream object failure aborts the
    stream with ``RuntimeError``; the client then observes a truncated/failed
    download instead of a silently partial ZIP.
    """
    dos_time, dos_date = _dos_time_date()
    central: list[bytes] = []
    offset = 0
    try:
        async with httpx.AsyncClient() as client:
            for bucket, object_key, archive_name, size, force_presigned in entries:
                name = archive_name.encode("utf-8")
                header = _zip64_local_header(name, dos_time, dos_date)
                header_offset = offset
                yield header
                offset += len(header)

                crc = 0
                written = 0
                try:
                    async for chunk in _afetch_object_chunks(
                        client,
                        bucket=bucket,
                        object_key=object_key,
                        size=size,
                        force_presigned=force_presigned,
                    ):
                        crc = binascii.crc32(chunk, crc)
                        written += len(chunk)
                        yield chunk
                        offset += len(chunk)
                except Exception as exc:
                    logger.error(
                        "Aborting streamed output ZIP on %s/%s: %s",
                        bucket,
                        object_key,
                        exc,
                    )
                    raise RuntimeError(
                        f"Failed to download object {bucket}/{object_key}: {exc}"
                    ) from exc

                descriptor = _zip64_data_descriptor(crc, written)
                yield descriptor
                offset += len(descriptor)
                central.append(
                    _zip64_central_entry(
                        name, crc, written, header_offset, dos_time, dos_date
                    )
                )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Could not stream output archive: {exc}") from exc

    central_offset = offset
    central_blob = b"".join(central)
    if central_blob:
        yield central_blob
        offset += len(central_blob)
    yield _zip64_end_records(len(central), len(central_blob), central_offset)
