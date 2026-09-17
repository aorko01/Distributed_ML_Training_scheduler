from contextlib import contextmanager
import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from .models import Base

SCHEMA_VERSION = 1


class Database:
    def __init__(self, url):
        self.url = url
        self.engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 5})
        @event.listens_for(self.engine, "connect")
        def configure(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA journal_mode=WAL")

    def migrate(self):
        filename = self.url.removeprefix("sqlite:///")
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        # SQLite online backup includes committed WAL content. Keep backups private.
        if Path(filename).exists() and Path(filename).stat().st_size:
            with sqlite3.connect(filename) as source, sqlite3.connect(filename + ".pre-migration") as target:
                source.backup(target)
            Path(filename + ".pre-migration").chmod(0o600)
        with self.engine.begin() as connection:
            version = connection.exec_driver_sql("PRAGMA user_version").scalar()
            if version not in (0, SCHEMA_VERSION):
                raise RuntimeError("unsupported database schema; no automatic reset")
            if version == 0:
                Base.metadata.create_all(connection)
                connection.exec_driver_sql(f"PRAGMA user_version={SCHEMA_VERSION}")
        Path(filename).chmod(0o600)

    def ready(self):
        with self.engine.connect() as connection:
            return connection.exec_driver_sql("PRAGMA user_version").scalar() == SCHEMA_VERSION

    @contextmanager
    def transaction(self):
        # Every invariant is checked after obtaining the write lock. This also
        # serializes concurrent HTTP requests/process connections across restart.
        with self.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            session = Session(bind=connection, expire_on_commit=False)
            try:
                yield session
                session.flush()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                session.close()


def main():
    from .config import Settings
    settings = Settings.from_env()
    Database(settings.database_url).migrate()


if __name__ == "__main__":
    main()
