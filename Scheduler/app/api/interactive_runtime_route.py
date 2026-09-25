from fastapi import APIRouter, Depends, Response, Request, HTTPException
from pydantic import BaseModel
from app.api.deps import get_db, get_current_active_user, get_cli_user, get_ssh_user
from app.api.interactive_workspace_route import key
from app.schemas.worker_execution_schema import Start
from app.models.interactive_workspace_model import WorkspaceTrainingSubmission
from app.services import interactive_runtime_service as service
from app.services import workspace_editor_service as workspace_service
from app.services import cli_auth_service as cli_auth
from app.schemas.workspace_editor_schema import SaveRequest, TrainingRequest, TrainingSettings
from app.services.interactive_management_client import ManagementClient


class CliLogin(BaseModel):
    username: str
    password: str


class CliRefresh(BaseModel):
    refresh_token: str


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


@router.get("/runtimes/mine")
def mine(user=Depends(get_current_active_user), db=Depends(get_db)):
    return service.active_for_owner(db, user.user_id)


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


@router.post("/runtimes/{runtime_id}/workspace-connection")
def workspace_connection(
    runtime_id: str,
    response: Response,
    user=Depends(get_current_active_user),
    db=Depends(get_db),
):
    """Issue a fresh, single-use grant for the pinned workspace service."""
    response.headers["Cache-Control"] = "no-store"
    try:
        client = ManagementClient()
    except (KeyError, OSError, ValueError):
        raise HTTPException(503, "Connection service unavailable") from None
    try:
        return service.connection(db, user.user_id, runtime_id, client, workspace=True)
    finally:
        client.close()


@router.get("/runtimes/{runtime_id}/ssh-info")
def ssh_info(runtime_id: str, user=Depends(get_ssh_user), db=Depends(get_db)):
    return service.ssh_info(db, user.user_id, runtime_id)


@router.post("/runtimes/{runtime_id}/ssh-connection")
def ssh_connection(
    runtime_id: str,
    response: Response,
    user=Depends(get_cli_user),
    db=Depends(get_db),
):
    """Fresh single-use SSH-purpose grant (scoped CLI token only)."""
    response.headers["Cache-Control"] = "no-store"
    try:
        client = ManagementClient()
    except (KeyError, OSError, ValueError):
        raise HTTPException(503, "Connection service unavailable") from None
    try:
        return service.ssh_connection(db, user.user_id, runtime_id, client)
    finally:
        client.close()


@router.post("/cli/login")
def cli_login(body: CliLogin, db=Depends(get_db)):
    if len(body.username) > 128 or len(body.password) > 256:
        raise HTTPException(422, "Invalid credentials")
    return cli_auth.login(db, body.username, body.password)


@router.post("/cli/refresh")
def cli_refresh(body: CliRefresh, db=Depends(get_db)):
    return cli_auth.refresh(db, body.refresh_token)


@router.post("/cli/logout")
def cli_logout(body: CliRefresh, db=Depends(get_db)):
    return cli_auth.logout(db, body.refresh_token)


@router.post("/runtimes/{runtime_id}/saves", status_code=202)
def save_workspace(
    runtime_id: str, body: SaveRequest, request_key=Depends(key),
    user=Depends(get_current_active_user), db=Depends(get_db),
):
    return workspace_service.create_save(db, user.user_id, runtime_id, request_key, body)


@router.get("/saves/{save_id}")
def save_status(save_id: str, user=Depends(get_current_active_user), db=Depends(get_db)):
    return workspace_service.get_save(db, user.user_id, save_id)


@router.post("/runtimes/{runtime_id}/save-and-stop", status_code=202)
def save_and_stop(
    runtime_id: str, body: SaveRequest, request_key=Depends(key),
    user=Depends(get_current_active_user), db=Depends(get_db),
):
    return workspace_service.save_and_stop(db, user.user_id, runtime_id, request_key, body)


@router.post("/runtimes/{runtime_id}/training-submissions", status_code=202)
def training_submission(
    runtime_id: str, body: TrainingRequest, request_key=Depends(key),
    user=Depends(get_current_active_user), db=Depends(get_db),
):
    return workspace_service.create_submission(db, user.user_id, runtime_id, request_key, body)


@router.get("/training-submissions/{submission_id}")
def training_status(submission_id: str, user=Depends(get_current_active_user), db=Depends(get_db)):
    item = db.query(WorkspaceTrainingSubmission).filter_by(id=submission_id, owner_user_id=user.user_id).first()
    if not item:
        raise HTTPException(404, "Training submission not found")
    return workspace_service.submission_public(item)


@router.post("/workspaces/{workspace_id}/revisions/{revision_id}/training-submissions", status_code=202)
def revision_training_submission(
    workspace_id: str, revision_id: str, body: TrainingSettings,
    request_key=Depends(key), user=Depends(get_current_active_user), db=Depends(get_db),
):
    return workspace_service.create_revision_submission(
        db, user.user_id, workspace_id, revision_id, request_key, body,
    )


@router.get("/workspaces/{workspace_id}/revisions")
def revision_history(workspace_id: str, after: int | None = None, user=Depends(get_current_active_user), db=Depends(get_db)):
    return workspace_service.revisions(db, user.user_id, workspace_id, after=after)
