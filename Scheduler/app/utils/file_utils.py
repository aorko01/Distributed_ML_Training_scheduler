import os
import uuid
import io
import zipfile

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

OBJECT_STORE_URL = os.environ.get("OBJECT_STORE_URL", "http://localhost:8010").rstrip(
    "/"
)
OBJECT_STORE_BUCKET = os.environ.get("OBJECT_STORE_BUCKET", "uploads")


def find_file_in_zip(names: list[str], filename: str) -> str | None:
    """Return the first entry in `names` whose basename matches `filename`,
    without touching the filesystem. Skips directory entries."""
    for name in names:
        if name.endswith("/"):
            continue
        if os.path.basename(name) == filename:
            return name
    return None


def validate_required_files(names: list[str], required_files: list[str]):
    for file in required_files:
        if not find_file_in_zip(names, file):
            raise FileNotFoundError(f"Required file '{file}' not found in ZIP.")


def save_to_object_store(
    file_content: bytes,
    filename: str,
    require_files: list[str] = None,
    job_id: str = None,
) -> dict:
    job_id = job_id or str(uuid.uuid4())

    zip_filename = filename if filename else f"{uuid.uuid4()}.zip"
    object_key = f"{job_id}/{zip_filename}"

    try:
        with zipfile.ZipFile(io.BytesIO(file_content), "r") as zip_ref:
            # Reads only the central directory listing — no decompression,
            # no writing to disk, so no zip-slip / zip-bomb exposure.
            extracted_files = zip_ref.namelist()

        zip_base_name = os.path.splitext(zip_filename)[0]
        prefix = f"{zip_base_name}/"
        matched_files = [f for f in extracted_files if f.startswith(prefix)]

        if require_files:
            validate_required_files(extracted_files, require_files)


    except zipfile.BadZipFile:
        raise zipfile.BadZipFile("Uploaded file is not a valid ZIP archive.")

    upload_response = requests.post(
        f"{OBJECT_STORE_URL}/objects/upload",
        data={
            "bucket": OBJECT_STORE_BUCKET,
            "object_key": object_key,
        },
        files={
            "file": (zip_filename, io.BytesIO(file_content), "application/zip"),
        },
        timeout=30,
    )

    if upload_response.status_code >= 400:
        raise RuntimeError(
            f"Object store upload failed: {upload_response.status_code} {upload_response.text}"
        )

    stored_object_key = upload_response.json().get("object_key", object_key)

    return {
        "object_key": stored_object_key,
        "files": matched_files,
    }