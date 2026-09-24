from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
from app.api.deps import get_db, get_current_active_user
from app.schemas.interactive_capacity_schema import ResourceRequirements
from app.services import interactive_capacity_service as service

router = APIRouter(prefix="/interactive/capacity", tags=["interactive capacity"])


@router.get("/options")
def options(response: Response, db: Session = Depends(get_db), user=Depends(get_current_active_user)):
    response.headers["Cache-Control"] = "no-store"
    return service.options_payload(db)


@router.post("/preview")
def preview(body: ResourceRequirements, response: Response, db: Session = Depends(get_db), user=Depends(get_current_active_user)):
    response.headers["Cache-Control"] = "no-store"
    return service.preview_payload(db, body, user.user_id)
