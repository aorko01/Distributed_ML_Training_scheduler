from fastapi import APIRouter

router = APIRouter(tags=["scheduler"])


@router.get("/health")
def health_check():
    return {"status": "ok", "service": "scheduler"}
