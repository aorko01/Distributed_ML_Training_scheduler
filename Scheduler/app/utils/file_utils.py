import os
import uuid
import zipfile

# Use absolute path based on app location
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


def find_file(directory: str, filename: str) -> str | None:
    """
    Recursively search for a file inside a directory.
    Returns full path if found, otherwise None.
    """
    for root, _, files in os.walk(directory):
        if filename in files:
            return os.path.join(root, filename)
    return None


def validate_required_files(extract_dir: str, required_files: list[str]):
    """
    Ensure required files exist inside extracted directory.
    Raises FileNotFoundError if any file is missing.
    """
    for file in required_files:
        file_path = find_file(extract_dir, file)
        if not file_path:
            raise FileNotFoundError(f"Required file '{file}' not found in ZIP.")


def save_and_extract_zip(
    file_content: bytes,
    filename: str,
    entry_file: str,
    require_files: list[str] = None
) -> dict:
    """
    Save uploaded ZIP file, extract it, and validate files.

    Returns:
        {
            "zip_path": str,
            "extract_dir": str,
            "files": list[str],
            "script_path": str
        }
    """

    job_id = str(uuid.uuid4())
    job_dir = os.path.join(UPLOAD_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    zip_filename = filename if filename else f"{uuid.uuid4()}.zip"
    zip_path = os.path.join(job_dir, zip_filename)

    # Save ZIP
    with open(zip_path, "wb") as f:
        f.write(file_content)

    extract_dir = os.path.join(job_dir, "extracted")
    os.makedirs(extract_dir, exist_ok=True)

    # Extract ZIP
    try:
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(extract_dir)
            extracted_files = zip_ref.namelist()
    except zipfile.BadZipFile:
        raise zipfile.BadZipFile("Uploaded file is not a valid ZIP archive.")

    # Validate required files (like requirements.txt)
    if require_files:
        validate_required_files(extract_dir, require_files)

    # Validate entry file
    script_path = find_file(extract_dir, entry_file)
    if not script_path:
        raise FileNotFoundError(f"Entry file '{entry_file}' not found in ZIP.")

    return {
        "zip_path": zip_path,
        "extract_dir": extract_dir,
        "files": extracted_files,
        "script_path": script_path
    }