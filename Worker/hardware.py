import os
import uuid
import GPUtil
from config import WORKER_ID_FILE

def get_or_create_worker_id() -> str:
    """Retrieve existing worker ID or generate a new persistent one."""
    if os.path.exists(WORKER_ID_FILE):
        with open(WORKER_ID_FILE, "r") as f:
            return f.read().strip()
    
    new_id = str(uuid.uuid4())
    with open(WORKER_ID_FILE, "w") as f:
        f.write(new_id)
    return new_id

def get_gpu_info():
    """Retrieve primary GPU specs and VRAM availability."""
    gpus = GPUtil.getGPUs()
    if not gpus:
        return "Unknown", 0.0, 0.0, 0
    
    gpu = gpus[0]
    gpu_name = gpu.name
    total_vram = round(gpu.memoryTotal / 1024, 2)
    free_vram = round(gpu.memoryFree / 1024, 2)
    num_gpus = len(gpus)
    
    return gpu_name, total_vram, free_vram, num_gpus