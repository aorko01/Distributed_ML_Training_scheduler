from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.api.deps import get_db, get_current_user
from app.models.user_model import User
from app.services import resource_service
from app.schemas.resource_schema import (
    ResourceOptions,
    ResourceConfig,
    ResourceSummaryResponse,
    ResourceRequestCreate,
    ResourceRequestResponse,
)

router = APIRouter(tags=["resources"])


@router.get("/options", response_model=ResourceOptions)
def get_resource_options(db: Session = Depends(get_db)):
    """Discrete resource values currently present in the workers database."""
    return resource_service.get_resource_options(db)


@router.post("/summary", response_model=ResourceSummaryResponse)
async def get_resource_summary(
    config: ResourceConfig, db: Session = Depends(get_db)
):
    """Matching machines, average running jobs, and queue length for a resource config."""
    return await resource_service.get_resource_summary(db, config)


@router.post("/request", response_model=ResourceRequestResponse)
def create_resource_request(
    payload: ResourceRequestCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Submit a resource request for the current user (enters the queue)."""
    request = resource_service.create_resource_request(
        db, str(current_user.user_id), payload
    )
    return ResourceRequestResponse(
        request_id=str(request.request_id),
        status=str(request.status),
        message=(
            f"Resource request {str(request.request_id)[:8]} queued. "
            "You will be notified when a matching machine is reserved for you."
        ),
        queue_open=resource_service.get_open_request_count(db),
    )