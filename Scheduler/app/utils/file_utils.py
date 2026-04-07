import os
import uuid
import shutil
import zipfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


def find_file(directory: str, filename: str) -> str | None:
    for root, _, files in os.walk(directory):
        if filename in files:
            return os.path.join(root, filename)
    return None


def validate_required_files(extract_dir: str, required_files: list[str]):
    for file in required_files:
        file_path = find_file(extract_dir, file)
        if not file_path:
            raise FileNotFoundError(f"Required file '{file}' not found in ZIP.")

def save_and_extract_zip(
    file_content: bytes,
    filename: str,
    entry_file: str,
    require_files: list[str] = None,
    job_id: str = None
) -> dict:
    job_id = job_id or str(uuid.uuid4())
    job_dir = os.path.join(UPLOAD_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    zip_filename = filename if filename else f"{uuid.uuid4()}.zip"
    zip_path = os.path.join(job_dir, zip_filename)

    # Get zip name without extension e.g. "Distribute_test"
    zip_base_name = os.path.splitext(zip_filename)[0]

    # Save ZIP
    with open(zip_path, "wb") as f:
        f.write(file_content)

    # Extract to temp dir
    temp_dir = os.path.join(job_dir, "__temp__")
    os.makedirs(temp_dir, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(temp_dir)
            extracted_files = zip_ref.namelist()
    except zipfile.BadZipFile:
        raise zipfile.BadZipFile("Uploaded file is not a valid ZIP archive.")

    # Match only entries that start with "Distribute_test/" i.e. zip_base_name/
    prefix = f"{zip_base_name}/"
    matched_files = [f for f in extracted_files if f.startswith(prefix)]

    # Move matched files into job_dir, stripping the prefix
    for relative_path in matched_files:
        src = os.path.join(temp_dir, relative_path)

        if not os.path.exists(src):
            continue

        # Strip the zip_base_name/ prefix to get the flat relative path
        # e.g. "Distribute_test/train.py" → "train.py"
        stripped = relative_path[len(prefix):]

        if not stripped:
            # This was the folder entry itself, skip
            continue

        dst = os.path.join(job_dir, stripped)
        os.makedirs(os.path.dirname(dst), exist_ok=True)

        if os.path.exists(dst):
            shutil.rmtree(dst) if os.path.isdir(dst) else os.remove(dst)

        shutil.move(src, dst)

    # Delete entire temp dir (removes everything including unmatched files)
    shutil.rmtree(temp_dir)
    os.remove(zip_path)

    if require_files:
        validate_required_files(job_dir, require_files)

    script_path = find_file(job_dir, entry_file)
    if not script_path:
        raise FileNotFoundError(f"Entry file '{entry_file}' not found in ZIP.")

    return {
        "extract_dir": job_dir,
        "files": matched_files,
        "script_path": script_path
    }