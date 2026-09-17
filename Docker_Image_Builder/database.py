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

def record_interactive_attempt(revision_id, attempt_id, image_tag, digest_ref=None, accepted=False):
    """Exact attempt artifact ledger for retention/orphan review, never wildcard prune."""
    with get_connection() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS interactive_artifacts (
            attempt_id TEXT PRIMARY KEY, revision_id TEXT NOT NULL,
            image_tag TEXT NOT NULL UNIQUE, digest_ref TEXT, accepted INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL)''')
        conn.execute('''INSERT INTO interactive_artifacts(attempt_id,revision_id,image_tag,digest_ref,accepted,updated_at)
            VALUES(?,?,?,?,?,?) ON CONFLICT(attempt_id) DO UPDATE SET
            digest_ref=COALESCE(excluded.digest_ref,interactive_artifacts.digest_ref),
            accepted=MAX(interactive_artifacts.accepted,excluded.accepted),updated_at=excluded.updated_at''',
            (attempt_id, revision_id, image_tag, digest_ref, int(accepted), datetime.now(timezone.utc).isoformat()))
