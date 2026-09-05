import os
import sqlite3
from datetime import datetime, timedelta, timezone
from config import DB_PATH


def get_connection():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db():
    with get_connection() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS processed_jobs (
                job_id TEXT PRIMARY KEY,
                processed_at TIMESTAMP
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS base_images (
                image_name TEXT PRIMARY KEY,
                last_used_at TIMESTAMP
            )
        ''')


def is_job_processed(job_id: str) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("SELECT 1 FROM processed_jobs WHERE job_id = ?", (job_id,))
        return cursor.fetchone() is not None


def mark_job_processed(job_id: str):
    with get_connection() as conn:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO processed_jobs (job_id, processed_at) VALUES (?, ?)",
            (job_id, now)
        )


def update_base_image_usage(image_name: str):
    with get_connection() as conn:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO base_images (image_name, last_used_at) VALUES (?, ?)",
            (image_name, now)
        )


def get_old_base_images(days: int = 7) -> list:
    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT image_name FROM base_images WHERE last_used_at < ?",
            (cutoff_date,)
        )
        return [row[0] for row in cursor.fetchall()]


def remove_base_image_record(image_name: str):
    with get_connection() as conn:
        conn.execute("DELETE FROM base_images WHERE image_name = ?", (image_name,))
