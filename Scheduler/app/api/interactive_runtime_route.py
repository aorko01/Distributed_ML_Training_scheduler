from fastapi import APIRouter, Depends, Response, Request, HTTPException
from app.api.deps import get_db, get_current_active_user
from app.api.interactive_workspace_route import key
from app.schemas.worker_execution_schema import Start
from app.services import interactive_runtime_service as service
from app.services.interactive_management_client import ManagementClient


async def bounded(request: Request):
    if len(await request.body()) > 2048:
        raise HTTPException(413, "Runtime request too large")


router = APIRouter(
    prefix="/interactive",
    dependencies=[Depends(bounded)],
    tags=["interactive runtimes"],
)


@router.post("/workspaces/{workspace_id}/runtimes", status_code=202)
def start(
    workspace_id: str,
    body: Start,
    request_key=Depends(key),
    user=Depends(get_current_active_user),
    db=Depends(get_db),
):
    return service.start(db, user.user_id, workspace_id, request_key, body)


@router.get("/workspaces/{workspace_id}/runtime")
def latest(
    workspace_id: str, user=Depends(get_current_active_user), db=Depends(get_db)
):
    return service.latest(db, user.user_id, workspace_id)


@router.post("/runtimes/{runtime_id}/stop", status_code=202)
def stop(runtime_id: str, user=Depends(get_current_active_user), db=Depends(get_db)):
    return service.stop(db, user.user_id, runtime_id)


@router.post("/runtimes/{runtime_id}/connection")
def connection(
    runtime_id: str,
    response: Response,
    user=Depends(get_current_active_user),
    db=Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    service.owned_runtime(db, user.user_id, runtime_id)
    try:
        client = ManagementClient()
    except (KeyError, OSError, ValueError):
        raise HTTPException(503, "Connection service unavailable") from None
    try:
        return service.connection(db, user.user_id, runtime_id, client)
    finally:
        client.close()
