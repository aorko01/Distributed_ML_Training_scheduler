from sqlalchemy.orm import Session
from app.models.worker import Worker

def register_or_update_worker(db: Session, worker_info):
    # Check if worker exists
    db_worker = db.query(Worker).filter(Worker.worker_id == worker_info.worker_id).first()

    if db_worker:
        # Update last_registered timestamp
        db_worker.last_registered = func.now()
        db.commit()
    else:
        # Insert new worker
        db_worker = Worker(
            worker_id=worker_info.worker_id,
            mac_address=worker_info.mac_address,
            gpu_type=worker_info.gpu_type,
            num_gpus=worker_info.num_gpus,
            total_vram=worker_info.total_vram,
        )
        db.add(db_worker)
        db.commit()