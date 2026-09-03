from sqlalchemy.orm import Session
from app.models.worker_model import Worker
from app.models.resource_request_model import ResourceRequest
from app.services.worker_service import get_all_workers
from app.schemas.resource_schema import (
    ResourceOptions,
    ResourceConfig,
    ResourceSummaryResponse,
    ResourceRequestCreate,
)

STATUS_PENDING = "PENDING"


def get_resource_options(db: Session) -> ResourceOptions:
    """Distinct resource values present in the workers table (only what is in the DB)."""
    gpu_types = (
        db.query(Worker.gpu_type)
        .filter(Worker.gpu_type.is_not(None))
        .distinct()
        .order_by(Worker.gpu_type)
        .all()
    )
    vram_options = (
        db.query(Worker.total_vram)
        .filter(Worker.total_vram.is_not(None))
        .distinct()
        .order_by(Worker.total_vram)
        .all()
    )
    ram_options = (
        db.query(Worker.total_ram)
        .filter(Worker.total_ram.is_not(None))
        .distinct()
        .order_by(Worker.total_ram)
        .all()
    )
    core_options = (
        db.query(Worker.cpu_cores)
        .filter(Worker.cpu_cores.is_not(None))
        .distinct()
        .order_by(Worker.cpu_cores)
        .all()
    )
    disk_options = (
        db.query(Worker.available_disk)
        .filter(Worker.available_disk.is_not(None))
        .distinct()
        .order_by(Worker.available_disk)
        .all()
    )

    return ResourceOptions(
        gpu_types=[r[0] for r in gpu_types if r[0]],
        vram_options=[float(r[0]) for r in vram_options if r[0] is not None],
        ram_options=[float(r[0]) for r in ram_options if r[0] is not None],
        core_options=[int(r[0]) for r in core_options if r[0] is not None],
        disk_options=[float(r[0]) for r in disk_options if r[0] is not None],
    )


def _int_or_float(value) -> float | None:
    if value is None:
        return None
    try:
        if isinstance(value, float) and value.is_integer():
            return float(value)
        return float(value)
    except (TypeError, ValueError):
        return None


def _compare(actual, requested, op: str) -> bool:
    """Compare a machine/request value against a requested amount under an operator."""
    if requested is None:
        return True
    if actual is None:
        return False
    actual_f, requested_f = float(actual), float(requested)
    if op == "eq":
        return abs(actual_f - requested_f) < 1e-9
    return actual_f >= requested_f


def _config_matches(
    gpu_type_value, vram_value, ram_value, cpu_value, disk_value, config: ResourceConfig
) -> bool:
    if config.gpu_type and gpu_type_value != config.gpu_type:
        return False
    if not _compare(vram_value, config.gpu_vram, config.op):
        return False
    if not _compare(ram_value, config.cpu_ram, config.op):
        return False
    if not _compare(cpu_value, config.cpu_cores, config.op):
        return False
    if not _compare(disk_value, config.disk, config.op):
        return False
    return True


async def get_resource_summary(db: Session, config: ResourceConfig) -> ResourceSummaryResponse:
    """Matching machine stats plus the resource-request queue length for a config."""
    workers = await get_all_workers(db)
    matched = [
        w
        for w in workers
        if _config_matches(
            gpu_type_value=w.get("gpu_type"),
            vram_value=w.get("total_vram"),
            ram_value=w.get("total_ram"),
            cpu_value=w.get("cpu_cores"),
            disk_value=w.get("available_disk"),
            config=config,
        )
    ]

    if matched:
        avg_running_jobs = sum(int(w.get("running_jobs") or 0) for w in matched) / len(matched)
    else:
        avg_running_jobs = 0.0

    requests = db.query(ResourceRequest).all()
    queue_total = 0
    queue_open = 0
    for req in requests:
        if not _config_matches(
            gpu_type_value=req.gpu_type,
            vram_value=req.gpu_vram,
            ram_value=req.cpu_ram,
            cpu_value=req.cpu_cores,
            disk_value=req.disk,
            config=config,
        ):
            continue
        queue_total += 1
        if req.closed_at is None:
            queue_open += 1

    return ResourceSummaryResponse(
        matching_nodes=len(matched),
        avg_running_jobs=round(avg_running_jobs, 2),
        queue_total=queue_total,
        queue_open=queue_open,
    )


def get_open_request_count(db: Session) -> int:
    """Number of resource requests that have not been closed yet."""
    return db.query(ResourceRequest).filter(ResourceRequest.closed_at.is_(None)).count()


def create_resource_request(
    db: Session, user_id: str, payload: ResourceRequestCreate
) -> ResourceRequest:
    """Insert a PENDING resource request for a user."""
    request = ResourceRequest(
        user_id=user_id,
        cpu_ram=payload.cpu_ram,
        gpu_vram=payload.gpu_vram,
        cpu_cores=payload.cpu_cores,
        gpu_type=payload.gpu_type,
        disk=payload.disk,
        status=STATUS_PENDING,
        notes=payload.notes,
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    return request
