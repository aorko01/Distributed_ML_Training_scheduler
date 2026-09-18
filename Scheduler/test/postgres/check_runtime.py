"""Real PostgreSQL upgrade, exclusion, fencing and concurrent claim gate."""

import os
from pathlib import Path
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def identifier():
    return str(uuid.uuid4())


def main():
    url = make_url(os.environ["DATABASE_URL"])
    if url.get_backend_name() != "postgresql":
        raise ValueError("PostgreSQL required")
    schema = "runtime_test_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as c:
        c.execute(text(f"CREATE SCHEMA {schema}"))
    os.environ["DATABASE_URL"] = url.update_query_dict(
        {"options": "-csearch_path=" + schema}
    ).render_as_string(hide_password=False)
    os.environ["INTERACTIVE_RUNTIME_ENABLED"] = "1"
    try:
        from app.db.database import engine, SessionLocal, run_migrations
        from app.models.user_model import User
        from app.models.job_model import Job, JobStatus
        from app.models.worker_model import Worker
        from app.models.interactive_workspace_model import (
            InteractiveWorkspace as Workspace,
            InteractiveImageRevision as Revision,
        )
        from app.models.interactive_runtime_model import (
            InteractiveRuntime as Runtime,
            WorkerAssignment as Assignment,
        )
        from app.schemas.worker_execution_schema import Claim, Fence, Start
        from app.services.scheduling import claims
        from app.services.scheduling.config import Settings
        from app.services.scheduling.types import now, Candidate, Kind
        from app.services import interactive_runtime_service as runtimes

        # An actual older schema, not create_all with the new model columns.
        with engine.begin() as c:
            for model in (User, Job):
                model.__table__.create(c)
            c.exec_driver_sql(
                "CREATE TABLE workers (worker_id VARCHAR PRIMARY KEY, gpu_type VARCHAR NOT NULL,num_gpus INTEGER NOT NULL,total_vram FLOAT NOT NULL,available_vram FLOAT,hostname VARCHAR,ip_address VARCHAR,gpu_load FLOAT,cpu_load FLOAT,mem_usage FLOAT,test_device BOOLEAN,first_seen TIMESTAMPTZ DEFAULT now(),last_registered TIMESTAMPTZ DEFAULT now())"
            )
            c.exec_driver_sql("CREATE TABLE resource_requests (id VARCHAR PRIMARY KEY)")
        run_migrations()
        run_migrations()
        settings = Settings(admission=True, interactive=True)

        def inv():
            return {
                "complete": True,
                "observed_at": now().timestamp(),
                "mode": "AVAILABLE",
                "available_slots": 2,
                "local_assignments": [],
                "free_vram_gb": 16.0,
                "free_ram_gb": 64.0,
                "free_disk_gb": 200.0,
                "cpu_cores": 16,
                "platform": "linux/amd64",
                "nvidia_runtime": True,
                "quota_supported": True,
                "interactive_ready": True,
                "gpus": [
                    {
                        "uuid": "GPU-fixture",
                        "model": "fixture",
                        "memory_gb": 16.0,
                        "busy": False,
                        "processes": [],
                    }
                ],
            }

        owner = identifier()
        workers = [identifier(), identifier()]
        instances = [identifier(), identifier()]
        with SessionLocal() as db:
            db.add(
                User(
                    user_id=owner,
                    username="gate",
                    email="gate@example.test",
                    name="Gate",
                    hashed_password="test",
                )
            )
            db.commit()
            for worker_id, instance in zip(workers, instances):
                db.add(
                    Worker(
                        worker_id=worker_id,
                        gpu_type="fixture",
                        num_gpus=1,
                        total_vram=16,
                        protocol_version=1,
                        instance_id=instance,
                        inventory=inv(),
                        authenticated_heartbeat_at=now(),
                        execution_mode="AVAILABLE",
                        execution_reconciling=False,
                    )
                )
            estimate = Job(
                id=identifier(),
                user_id=owner,
                object_key=identifier(),
                command="python train.py",
                docker_base_image="fixture",
                status=JobStatus.VRAM_ESTIMATION_PENDING,
            )
            db.add(estimate)
            db.commit()
            job_id = estimate.id

        def claim(pair):
            worker_id, instance = pair
            with SessionLocal() as db:
                return claims.claim(
                    db,
                    worker_id,
                    Claim(instance_id=instance, request_id=identifier()),
                    settings,
                )["assignment"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, zip(workers, instances)))
        assigned = [r for r in results if r]
        assert len(assigned) == 1 and assigned[0]["kind"] == "vram_estimation"
        a = assigned[0]
        worker_id = workers[instances.index(a["instance_id"])]
        fence = Fence(
            **{
                k: a[k]
                for k in ("instance_id", "assignment_id", "attempt_token", "generation")
            }
        )
        with SessionLocal() as db:
            claims.cleanup(db, worker_id, fence)
            db.get(Job, job_id).status = JobStatus.NOT_RUNNABLE
            db.commit()
            workspace_id, revision_id = identifier(), identifier()
            db.add(
                Workspace(
                    id=workspace_id,
                    owner_user_id=owner,
                    name="Gate",
                    source_type="UPLOAD",
                    request_key=identifier(),
                    request_hash="a" * 64,
                )
            )
            db.commit()
            db.add(
                Revision(
                    id=revision_id,
                    workspace_id=workspace_id,
                    revision_number=1,
                    origin="UPLOAD",
                    state="IMAGE_READY",
                    source_object_key="fixture.zip",
                    requested_base_image="fixture",
                    resolved_base_digest="fixture@sha256:" + "a" * 64,
                    image_tag="fixture:test",
                    image_digest_ref="fixture/workload@sha256:" + "a" * 64,
                )
            )
            db.commit()
            db.get(Workspace, workspace_id).current_revision_id = revision_id
            db.commit()
            runtime = runtimes.start(db, owner, workspace_id, identifier(), Start())
            runtime_id = runtime["id"]
            batch = Job(
                id=identifier(),
                user_id=owner,
                object_key=identifier(),
                command="python train.py",
                docker_base_image="fixture",
                status=JobStatus.RUNNABLE,
            )
            db.add(batch)
            db.commit()
            batch_id = batch.id

        class Forced:
            def __init__(self, kind):
                self.kind = kind

            def choose(self, *args):
                return Candidate(
                    self.kind,
                    runtime_id if self.kind == Kind.INTERACTIVE else batch_id,
                    "training",
                    "GPU-fixture" if self.kind == Kind.INTERACTIVE else None,
                )

        def race(kind):
            with SessionLocal() as db:
                return claims.claim(
                    db,
                    workers[0],
                    Claim(instance_id=instances[0], request_id=identifier()),
                    settings,
                    Forced(kind),
                )["assignment"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(race, [Kind.INTERACTIVE, Kind.BATCH]))
        active = [r for r in results if r]
        assert len(active) == 1, "Batch and interactive overlapped on one Worker"
        with SessionLocal() as db:
            holds = (
                db.query(Assignment)
                .filter_by(worker_id=workers[0], released_at=None)
                .all()
            )
            assert len(holds) == 1
            exact = Fence(
                **{
                    k: active[0][k]
                    for k in (
                        "instance_id",
                        "assignment_id",
                        "attempt_token",
                        "generation",
                    )
                }
            )
            if active[0]["kind"] == "interactive_access":
                runtimes.stop(db, owner, runtime_id)
                assert not claims.cleanup(db, workers[0], exact)["released"]
                db.get(Runtime, runtime_id).management_revoked = True
                db.commit()
            claims.cleanup(db, workers[0], exact)
            assert db.get(Assignment, exact.assignment_id).released_at
            # Production triggers freeze released tombstones and launch identity.
            try:
                db.execute(
                    text(
                        "UPDATE worker_assignments SET attempt_token=:token WHERE id=:id"
                    ),
                    {"token": identifier(), "id": exact.assignment_id},
                )
                db.commit()
            except DBAPIError:
                db.rollback()
            else:
                raise AssertionError("Released assignment identity was mutable")
        # One persisted controller lease must fence multiple processes. Supply
        # an actual assigned runtime regardless of which cross-kind claim won.
        from app.services import interactive_controller as controller
        import multiprocessing

        with SessionLocal() as db:
            current = db.get(Runtime, runtime_id)
            if current.state in ("STOPPED", "FAILED"):
                current = db.get(
                    Runtime,
                    runtimes.start(db, owner, workspace_id, identifier(), Start())[
                        "id"
                    ],
                )
            if current.state == "QUEUED":
                claim((workers[1], instances[1]))
            db.refresh(current)
            assert current.assignment_id
            current.state = "CONNECTING"
            current.enrollment_id = "exact-enrollment"
            current.enrollment_started = True
            current.health_at = now()
            current.health = {
                key: True for key in ("workload", "broker", "access", "endpoint")
            }
            db.commit()
        process_context = multiprocessing.get_context("fork")
        calls = process_context.Queue()
        entered = process_context.Event()
        proceed = process_context.Event()

        class Management:
            def call(self, method, path, body=None):
                calls.put((method, path))
                if path.endswith("/confirm"):
                    entered.set()
                    assert proceed.wait(10), "Reconciler race timed out"
                return {"state": "READY", "version": "exact-version"}

        def reconcile(output):
            # A fork must never reuse the parent's PostgreSQL connections.
            engine.dispose(close=False)
            with SessionLocal() as db:
                output.put(controller.reconcile_one(db, Management()))
            engine.dispose()

        first_result, second_result = process_context.Queue(), process_context.Queue()
        first = process_context.Process(target=reconcile, args=(first_result,))
        second = process_context.Process(target=reconcile, args=(second_result,))
        try:
            first.start()
            assert entered.wait(3)
            second.start()
            assert second_result.get(timeout=5) is False
            proceed.set()
            assert first_result.get(timeout=5) is True
            first.join(5)
            second.join(5)
            assert first.exitcode == second.exitcode == 0
        finally:
            proceed.set()
            for process in (first, second):
                if process.pid is not None:
                    process.join(1)
                    if process.is_alive():
                        process.terminate()
                        process.join(3)
        from queue import Empty

        observed = []
        while True:
            try:
                observed.append(calls.get(timeout=0.1))
            except Empty:
                break
        assert sum(path.endswith("/confirm") for _, path in observed) == 1
        print(
            "PostgreSQL upgrade/replay, estimation uniqueness, cross-kind Worker race and cleanup fencing and multiple reconcilers passed"
        )
        engine.dispose()
    finally:
        with admin.begin() as c:
            c.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()


if __name__ == "__main__":
    main()
