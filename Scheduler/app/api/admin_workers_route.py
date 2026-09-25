from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_superuser, get_db
from app.models.user_model import User
from app.models.worker_credential_model import WorkerCredential
from app.services import worker_credential_service as creds

router = APIRouter(prefix="/admin/workers", tags=["admin-workers"])


class WorkerCredentialCreate(BaseModel):
    worker_id: str = Field(min_length=1, max_length=128)
    secret: str = Field(min_length=32, max_length=256)


class WorkerCredentialResponse(BaseModel):
    worker_id: str
    source: str
    num_secrets: int


@router.get("/credentials", response_model=list[WorkerCredentialResponse])
def list_worker_credentials(
    _: User = Depends(get_current_superuser),
    db: Session = Depends(get_db),
):
    """List registered worker identities (secrets are never returned)."""
    return creds.list_credentials(db)


@router.post(
    "/credentials",
    response_model=WorkerCredentialResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_worker_credential(
    body: WorkerCredentialCreate,
    _: User = Depends(get_current_superuser),
    db: Session = Depends(get_db),
):
    """Register a (worker_id, secret) pair pasted in by the admin.

    Takes effect immediately: worker_auth checks the DB first, so no file
    edit or Scheduler restart is needed.
    """
    worker_id = body.worker_id.strip()
    err = creds.validate_worker_id(worker_id)
    if err:
        raise HTTPException(status_code=400, detail=err)
    err = creds.validate_secret(body.secret)
    if err:
        raise HTTPException(status_code=400, detail=err)
    try:
        creds.add_credential(db, worker_id, body.secret)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    row = (
        db.query(WorkerCredential)
        .filter(WorkerCredential.worker_id == worker_id)
        .first()
    )
    secrets = row.secrets if row is not None and isinstance(row.secrets, list) else []
    return WorkerCredentialResponse(
        worker_id=worker_id, source="db", num_secrets=len(secrets)
    )


@router.delete("/credentials/{worker_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_worker_credential(
    worker_id: str,
    _: User = Depends(get_current_superuser),
    db: Session = Depends(get_db),
):
    """Revoke all DB secrets for a worker (takes effect on next request)."""
    if not creds.remove_credential(db, worker_id.strip()):
        raise HTTPException(status_code=404, detail="Worker credential not found")
    return None
