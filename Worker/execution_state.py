"""One host lock and crash-safe journal shared by admission and execution."""

import json
from pathlib import Path
import sqlite3
import threading
import time
from uuid import uuid4

try:
    import fcntl
except ImportError:  # Windows / platforms without fcntl
    fcntl = None

MODES_BLOCKING = {
    "RECONCILING",
    "INTERACTIVE_RESERVED",
    "INTERACTIVE_ACTIVE",
    "CLEANING",
    "UNCERTAIN",
}


class Coordinator:
    def __init__(self, state_dir, clock=time.monotonic, host_lock_path=None):
        self.path = Path(state_dir)
        try:
            self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
        except PermissionError as exc:
            raise PermissionError(
                f"Cannot create state dir '{self.path}': permission denied. "
                f"Set WORKER_STATE_DIR to a writable directory, or create it first: "
                f"sudo install -d -m 0700 -o $USER '{self.path}'"
            ) from exc
        lock_path = Path(host_lock_path) if host_lock_path else (self.path / "service.lock")
        try:
            if lock_path.parent != self.path:
                lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.host_lock = open(lock_path, "a")
        except PermissionError as exc:
            raise PermissionError(
                f"Cannot create lock file '{lock_path}': permission denied. "
                f"Set WORKER_HOST_LOCK to a writable path, or create it first: "
                f"sudo install -d -m 0755 -o $USER '{lock_path.parent}'"
            ) from exc
        if fcntl is not None:
            try:
                fcntl.flock(self.host_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError) as exc:
                self.host_lock.close()
                raise
        else:
            try:
                import msvcrt

                msvcrt.locking(self.host_lock.fileno(), msvcrt.LK_NBLCK, 1)
            except (ImportError, OSError):
                pass  # Best-effort single-instance guard on platforms w/o flock.
        self.lock = threading.RLock()
        self.clock = clock
        self.instance_id = str(uuid4())
        self.mode = "RECONCILING"
        self.paused = False
        self.draining = False
        self.claim_pending = False
        self.deadlines = {}
        self.renew_sequences = {}
        self.db = sqlite3.connect(
            self.path / "assignments.sqlite", check_same_thread=False
        )
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS journal(id TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT)"
        )
        self.db.commit()

    def count(self, key):
        with self.lock, self.db:
            row = self.db.execute(
                "SELECT value FROM metadata WHERE key=?", (key,)
            ).fetchone()
            self.db.execute(
                "INSERT OR REPLACE INTO metadata VALUES (?,?)",
                (key, str(int(row[0]) + 1 if row else 1)),
            )

    def records(self):
        with self.lock:
            return [
                json.loads(row[0])
                for row in self.db.execute("SELECT data FROM journal")
            ]

    def persist(self, record):
        with self.lock, self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO journal VALUES (?,?)",
                (record["assignment_id"], json.dumps(record)),
            )

    def update(self, assignment_id, **values):
        with self.lock:
            record = self.get(assignment_id)
            record.update(values)
            self.persist(record)
            return record

    def get(self, assignment_id):
        with self.lock:
            row = self.db.execute(
                "SELECT data FROM journal WHERE id=?", (assignment_id,)
            ).fetchone()
            if not row:
                raise ValueError("Unknown assignment")
            return json.loads(row[0])

    def may_request_work(self):
        with self.lock:
            return (
                not self.paused
                and not self.draining
                and self.mode not in MODES_BLOCKING
                and not self.claim_pending
            )

    def available(self):
        with self.lock:
            active = [r for r in self.records() if not r.get("released")]
            if any(r.get("local_clean") or r.get("uncertain") for r in active):
                self.mode = "CLEANING"
            elif any(r["kind"] == "interactive_access" for r in active):
                self.mode = "INTERACTIVE_ACTIVE"
            else:
                self.mode = "BATCH_ACTIVE" if active else "AVAILABLE"

    def begin_claim(self):
        with self.lock:
            if not self.may_request_work():
                return None
            self.claim_pending = True
            row = self.db.execute(
                "SELECT value FROM metadata WHERE key='claim'"
            ).fetchone()
            request = (
                json.loads(row[0])
                if row
                else {"instance_id": self.instance_id, "request_id": str(uuid4())}
            )
            if request["instance_id"] != self.instance_id:
                self.mode = "UNCERTAIN"
                self.claim_pending = False
                return None
            with self.db:
                self.db.execute(
                    "INSERT OR REPLACE INTO metadata VALUES ('claim',?)",
                    (json.dumps(request),),
                )
            return request

    def accept(self, envelope, sent_at):
        with self.lock:
            self.claim_pending = False
            assignment = envelope.get("assignment")
            if assignment:
                active = [r for r in self.records() if not r.get("released")]
                conflict = assignment["kind"] == "interactive_access" and bool(active)
                assignment.update(
                    local_clean=False,
                    released=False,
                    containers={},
                    event_sequence=0,
                    uncertain=conflict,
                )
                self.persist(assignment)
                self.deadlines[assignment["assignment_id"]] = (
                    sent_at + assignment["lease_seconds"] - 5
                )
                assignment["lease_deadline_monotonic"] = self.deadlines[
                    assignment["assignment_id"]
                ]
                assignment["boot_id"] = (
                    Path("/proc/sys/kernel/random/boot_id").read_text().strip()
                )
                self.persist(assignment)
                if (
                    conflict
                    or self.clock() >= self.deadlines[assignment["assignment_id"]]
                ):
                    self.mode = "UNCERTAIN"
                elif assignment["kind"] == "interactive_access":
                    self.mode = "INTERACTIVE_RESERVED"
                else:
                    self.mode = "BATCH_ACTIVE"
            with self.db:
                self.db.execute("DELETE FROM metadata WHERE key='claim'")
            return assignment

    def claim_failed(self):
        with self.lock:
            self.claim_pending = False
            # Same durable request UUID is retried; no second reservation.

    def authoritative(self, assignment_id):
        with self.lock:
            record = self.get(assignment_id)
            return (
                not record.get("local_clean")
                and not record.get("uncertain")
                and not self.draining
                and self.clock() < self.deadlines.get(assignment_id, 0)
            )

    def renew(self, assignment_id, sent_at, budget, sequence):
        with self.lock:
            if sequence <= self.renew_sequences.get(
                assignment_id, 0
            ) or not self.authoritative(assignment_id):
                return False
            deadline = sent_at + budget - 5
            if deadline <= self.clock():
                return False
            self.deadlines[assignment_id] = deadline
            self.update(assignment_id, lease_deadline_monotonic=deadline)
            self.renew_sequences[assignment_id] = sequence
            return True

    def mark_clean(self, assignment_id):
        self.update(assignment_id, local_clean=True)
        with self.lock:
            self.mode = "CLEANING"

    def released(self, assignment_id):
        record = self.get(assignment_id)
        if not record.get("local_clean"):
            raise ValueError("Cleanup must precede release")
        self.update(assignment_id, released=True)
        self.available()

    def close(self):
        self.db.close()
        self.host_lock.close()
