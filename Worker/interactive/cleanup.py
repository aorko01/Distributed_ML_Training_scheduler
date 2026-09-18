"""Independent supervised lease guard / ExecStopPost exact-runtime cleanup."""

import argparse
import json
import os
from pathlib import Path
import sqlite3
import time
import docker


def sweep(state_dir, worker_id, force=False):
    path = Path(state_dir) / "assignments.sqlite"
    if not path.exists():
        return
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    client = docker.from_env(timeout=5)
    with sqlite3.connect(path, timeout=5) as db:
        rows = list(db.execute("SELECT id,data FROM journal"))
    for assignment_id, data in rows:
        record = json.loads(data)
        if record.get("released"):
            continue
        expired = (
            record.get("boot_id") != boot_id
            or record.get("lease_deadline_monotonic", 0) <= time.monotonic()
        )
        if not force and not expired:
            continue
        # Labels + exact journal identity protect unrelated objects. No prune,
        # volume/image deletion, or unsafe file paths from Docker metadata.
        objects = client.containers.list(
            all=True,
            filters={
                "label": ["dml.assignment=" + assignment_id, "dml.worker=" + worker_id]
            },
        )
        for c in objects:
            p = record["payload"]
            if record["kind"] == "interactive_access" and (
                c.labels.get("dml.runtime") != p["runtime_id"]
                or c.labels.get("dml.generation") != str(p["generation"])
            ):
                raise RuntimeError("Label identity mismatch; quarantine")
            c.remove(force=True, v=False)
        # A concurrent late create is handled by the next sweep/startup. Only
        # the execution owner can record a cleanup tombstone once tasks finish.


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true")
    parser.add_argument(
        "--state-dir", default=os.getenv("WORKER_STATE_DIR", "/var/lib/dml-worker")
    )
    args = parser.parse_args()
    worker_id = Path(os.environ["WORKER_ID_FILE"]).read_text().strip()
    while True:
        try:
            sweep(args.state_dir, worker_id, force=not args.watch)
        except Exception:
            if not args.watch:
                raise
        if not args.watch:
            return
        time.sleep(1)


if __name__ == "__main__":
    main()
