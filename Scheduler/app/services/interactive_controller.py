"""Persisted reconciliation lease. Network calls always run outside transactions."""

import asyncio
from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy import or_
from app.db.database import SessionLocal
from app.models.interactive_runtime_model import (
    InteractiveRuntime as Runtime,
    WorkerAssignment as Assignment,
)
from app.models.interactive_workspace_model import new_id
from .scheduling.types import now, utc
from .scheduling.claims import lock_worker, release_if_clean, stop_runtime, expire
from .interactive_management_client import ManagementClient


def bootstrap(db, worker_id, body, management):
    from .scheduling.claims import fence

    _, assignment = fence(db, worker_id, body)
    if not assignment.runtime_id:
        raise HTTPException(409, "Interactive bootstrap required")
    runtime = (
        db.query(Runtime).filter_by(id=assignment.runtime_id).with_for_update().one()
    )
    if runtime.desired_state != "RUNNING" or runtime.state not in (
        "STARTING",
        "CONNECTING",
    ):
        raise HTTPException(409, "Runtime stopping")
    if runtime.controller_until and utc(runtime.controller_until) > now():
        raise HTTPException(503, "Enrollment reconciliation in progress")
    token = new_id()
    runtime.enrollment_started = True
    runtime.controller_token, runtime.controller_until = token, now() + timedelta(
        seconds=20
    )
    resource, generation, enrollment = (
        runtime.resource_id,
        runtime.generation,
        runtime.enrollment_id,
    )
    db.commit()
    try:
        if enrollment:
            status = management.call("GET", "enrollments/" + enrollment)
            if status["state"] == "CONFIRMED":
                response = {"already_enrolled": True}
            else:
                response = management.call(
                    "POST",
                    "enrollments",
                    {
                        "role": "endpoint",
                        "identity": resource,
                        "generation": str(generation),
                    },
                    "runtime-" + resource,
                )
        else:
            response = management.call(
                "POST",
                "enrollments",
                {
                    "role": "endpoint",
                    "identity": resource,
                    "generation": str(generation),
                },
                "runtime-" + resource,
            )
        db.expire_all()
        worker = lock_worker(db, worker_id)
        runtime = (
            db.query(Runtime)
            .filter_by(id=assignment.runtime_id)
            .with_for_update()
            .one()
        )
        runtime.enrollment_id = response.get("enrollment_id", enrollment)
        stopped = (
            runtime.desired_state != "RUNNING"
            or runtime.controller_token != token
            or utc(runtime.controller_until) <= now()
            or worker.instance_id != body.instance_id
            or utc(db.get(Assignment, body.assignment_id).lease_until) <= now()
        )
        if runtime.controller_token == token:
            runtime.controller_token, runtime.controller_until = None, None
        db.commit()
        if stopped:
            if response.get("enrollment_id"):
                management.call("DELETE", "enrollments/" + response["enrollment_id"])
            raise HTTPException(409, "Runtime changed during enrollment")
        return response
    finally:
        db.rollback()
        db.query(Runtime).filter_by(
            resource_id=resource, controller_token=token
        ).update({"controller_token": None, "controller_until": None})
        db.commit()


def reconcile_one(db, management):
    candidate = (
        db.query(Runtime.id)
        .filter(
            Runtime.assignment_id.isnot(None),
            ~Runtime.state.in_(["STOPPED", "FAILED"]),
            or_(
                Runtime.state.in_(["CONNECTING", "READY", "LOST", "STOPPING"]),
                Runtime.desired_state == "STOPPED",
                Runtime.startup_deadline <= now(),
            ),
            or_(Runtime.controller_until.is_(None), Runtime.controller_until <= now()),
        )
        .order_by(Runtime.health_at.asc().nullsfirst(), Runtime.id)
        .first()
    )
    if not candidate:
        db.rollback()
        return False
    # Lock Worker before runtime. Competing reconcilers recheck after the lock.
    runtime = db.get(Runtime, candidate[0])
    assignment = db.get(Assignment, runtime.assignment_id)
    lock_worker(db, assignment.worker_id)
    runtime = (
        db.query(Runtime)
        .filter_by(id=runtime.id)
        .populate_existing()
        .with_for_update()
        .one()
    )
    if runtime.controller_until and utc(runtime.controller_until) > now():
        db.rollback()
        return False
    if (
        runtime.state == "LOST"
        or utc(assignment.lease_until) <= now()
        or (
            runtime.startup_deadline
            and runtime.state != "READY"
            and utc(runtime.startup_deadline) <= now()
        )
    ):
        stop_runtime(
            runtime, "LEASE_LOST" if runtime.state == "LOST" else "START_FAILED"
        )
    fresh = runtime.health_at and utc(runtime.health_at) > now() - timedelta(seconds=15)
    healthy = all(
        runtime.health.get(x) is True
        for x in ("workload", "broker", "access", "endpoint")
    )
    if runtime.state == "READY" and (not fresh or not healthy):
        stop_runtime(runtime, "HEALTH_FAILED")
    token = new_id()
    runtime.controller_token, runtime.controller_until = token, now() + timedelta(
        seconds=20
    )
    resource, enrollment, generation, owner, desired = (
        runtime.resource_id,
        runtime.enrollment_id,
        runtime.generation,
        runtime.owner_user_id,
        runtime.desired_state,
    )
    runtime_id, assignment_id = runtime.id, assignment.id
    phase, enrollment_started, worker_id = (
        runtime.state,
        runtime.enrollment_started,
        assignment.worker_id,
    )
    db.commit()
    endpoint = None
    try:
        if desired == "STOPPED":
            # DELETE denies locally immediately. Exact enrollment status confirms
            # remote key/node cleanup before allowing host capacity release.
            if not enrollment_started:
                revoked = True
            elif enrollment:
                # Check status BEFORE issuing deletes: DELETE re-arms REVOKING,
                # so a deny-then-check sequence can never observe the
                # background finalizer's REVOKED and livelocks with
                # management_revoked stuck False (blocking release and every
                # later claim). Only deny when not yet revoked.
                status = management.call("GET", "enrollments/" + enrollment)
                if status["state"] not in ("REVOKED", "EXPIRED"):
                    try:
                        management.call("DELETE", "resources/" + resource)
                    except HTTPException as exc:
                        if exc.status_code != 404:
                            raise
                    management.call("DELETE", "enrollments/" + enrollment)
                    status = management.call("GET", "enrollments/" + enrollment)
                revoked = status["state"] in ("REVOKED", "EXPIRED")
            else:
                # Enrollment may have succeeded before Scheduler persisted its ID.
                # Recover same-key result before declaring absence; ambiguity holds.
                try:
                    response = management.call(
                        "POST",
                        "enrollments",
                        {
                            "role": "endpoint",
                            "identity": resource,
                            "generation": str(generation),
                        },
                        "runtime-" + resource,
                    )
                    enrollment = response["enrollment_id"]
                    management.call("DELETE", "enrollments/" + enrollment)
                    revoked = False
                except HTTPException as exc:
                    if exc.status_code == 410:
                        revoked = True
                    else:
                        raise
        elif enrollment and phase in ("CONNECTING", "READY") and fresh and healthy:
            management.call(
                "POST",
                "enrollments/" + enrollment + "/confirm",
                {"generation": str(generation)},
            )
            endpoint = management.call(
                "PUT",
                "resources/" + resource + "/endpoint",
                {
                    "generation": str(generation),
                    "owner": owner,
                    "enrollment_id": enrollment,
                    "service": runtime.access_service,
                    "protocol": "tcp-stream-v1",
                    "port": 9000,
                },
            )
            endpoint = management.call(
                "POST",
                "resources/" + resource + "/lease",
                {"generation": str(generation)},
            )
        db.expire_all()
        worker = lock_worker(db, worker_id)
        runtime = db.query(Runtime).filter_by(id=runtime_id).with_for_update().one()
        if runtime.controller_token != token or utc(runtime.controller_until) <= now():
            db.rollback()
            return True
        if desired == "STOPPED":
            runtime.enrollment_id = enrollment
            runtime.management_revoked = revoked
            release_if_clean(db, worker, db.get(Assignment, assignment_id))
        elif (
            endpoint
            and runtime.desired_state == "RUNNING"
            and runtime.health_at
            and utc(runtime.health_at) > now() - timedelta(seconds=15)
            and all(
                runtime.health.get(x) is True
                for x in ("workload", "broker", "access", "endpoint")
            )
            and db.get(Assignment, assignment_id).state not in ("LOST", "CLEANING")
            and utc(db.get(Assignment, assignment_id).lease_until) > now()
        ):
            runtime.endpoint_version = endpoint["version"]
            if endpoint["state"] == "READY":
                runtime.state = "READY"
                runtime.ready_at = runtime.ready_at or now()
        elif runtime.desired_state == "STOPPED" and endpoint:
            db.commit()
            management.call("DELETE", "resources/" + resource)
        db.commit()
    except HTTPException as exc:
        db.rollback()
        if exc.status_code in (409, 410, 422):
            lock_worker(db, worker_id)
            runtime = db.query(Runtime).filter_by(id=runtime_id).with_for_update().one()
            stop_runtime(runtime, "START_FAILED")
            db.commit()
    finally:
        db.rollback()
        # Backoff each selected runtime so a stalled enrollment cannot starve
        # another runtime when several control-plane processes run.
        db.query(Runtime).filter_by(id=runtime_id, controller_token=token).update(
            {"controller_token": None, "controller_until": now() + timedelta(seconds=5)}
        )
        db.commit()
    return True


def tick():
    with SessionLocal() as db:
        expire(db)
    try:
        management = ManagementClient()
    except (KeyError, OSError, ValueError):
        return
    try:
        for _ in range(64):
            with SessionLocal() as db:
                if not reconcile_one(db, management):
                    break
    finally:
        management.close()


async def run():
    while True:
        try:
            await asyncio.to_thread(tick)
        except Exception:
            # No raw transport/credential-bearing exception logging.
            pass
        await asyncio.sleep(5)
