import os
import sqlite3
from datetime import datetime, timedelta, timezone
from config import DB_PATH, logger

def get_connection():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    return sqlite3.connect(DB_PATH)

def init_db():
    with get_connection() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS base_images (
                image_name TEXT PRIMARY KEY,
                last_used_at TIMESTAMP
            )
        ''')

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