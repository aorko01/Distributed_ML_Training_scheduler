from fastapi import APIRouter, Depends, Request, HTTPException
from app.api.deps import get_db
from .worker_execution_route_auth import worker_auth
from app.schemas.worker_execution_schema import (
    Logs,
    Register,
    Claim,
    Heartbeat,
    Event,
    Result,
    Fence,
    Cleanup,
)
from app.services.scheduling import claims
from app.services import interactive_controller
from app.services.interactive_management_client import ManagementClient


async def bounded_body(request: Request):
    length = 0
    async for chunk in request.stream():
        length += len(chunk)
        if length > 65536:
            raise HTTPException(413, "Worker request too large")


router = APIRouter(
    prefix="/internal/workers/v1",
    dependencies=[Depends(bounded_body)],
    tags=["worker execution"],
)


@router.post("/register")
def register(body: Register, worker=Depends(worker_auth), db=Depends(get_db)):
    return claims.register(db, worker, body)


@router.post("/claim")
def claim(body: Claim, worker=Depends(worker_auth), db=Depends(get_db)):
    return claims.claim(db, worker, body)


@router.post("/heartbeat")
def heartbeat(body: Heartbeat, worker=Depends(worker_auth), db=Depends(get_db)):
    return claims.heartbeat(db, worker, body)


@router.post("/event")
def event(body: Event, worker=Depends(worker_auth), db=Depends(get_db)):
    return claims.event(db, worker, body)


@router.post("/result")
def result(body: Result, worker=Depends(worker_auth), db=Depends(get_db)):
    return claims.result(db, worker, body)


@router.post("/cleanup")
def cleanup(body: Cleanup, worker=Depends(worker_auth), db=Depends(get_db)):
    return claims.cleanup(db, worker, body)


@router.post("/bootstrap")
def bootstrap(body: Fence, worker=Depends(worker_auth), db=Depends(get_db)):
    claims.fence(db, worker, body)
    db.rollback()
    try:
        client = ManagementClient()
    except (KeyError, OSError, ValueError):
        raise HTTPException(503, "Endpoint service unavailable") from None
    try:
        return interactive_controller.bootstrap(db, worker, body, client)
    finally:
        client.close()


@router.post("/logs")
async def logs(body: Logs, worker=Depends(worker_auth), db=Depends(get_db)):
    from app.services.log_service import publish_log_lines

    _, assignment = claims.fence(db, worker, body)
    if not assignment.job_id:
        raise HTTPException(409, "Batch logs required")
    job_id = assignment.job_id
    db.commit()
    try:
        await publish_log_lines(job_id, body.lines)
    except Exception:
        raise HTTPException(503, "Log stream unavailable") from None
    return {"accepted": True}
