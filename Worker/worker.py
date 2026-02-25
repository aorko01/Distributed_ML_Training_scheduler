import requests 
import uuid
import GPUtil
import json
import os
from dotenv import load_dotenv

load_dotenv() 

SCHEDULER_URL = os.getenv(
    "SCHEDULER_URL",
    "http://100.65.230.128:8000/workers/register"
)

# Persistent worker ID stored locally
WORKER_ID_FILE = "worker_id.txt"

if os.path.exists(WORKER_ID_FILE):
    with open(WORKER_ID_FILE, "r") as f:
        worker_id = f.read().strip()
else:
    worker_id = str(uuid.uuid4())
    with open(WORKER_ID_FILE, "w") as f:
        f.write(worker_id)

# Collect GPU info
gpus = GPUtil.getGPUs()
gpu = gpus[0]  # assume single GPU for now
gpu_type = gpu.name
total_vram = round(gpu.memoryTotal / 1024, 2)
num_gpus = len(gpus)

# MAC address
import uuid as u
mac = ':'.join(f'{(u.getnode() >> ele) & 0xff:02x}' for ele in range(0,8*6,8)[::-1])

worker_info = {
    "worker_id": worker_id,
    "mac_address": mac,
    "gpu_type": gpu_type,
    "num_gpus": num_gpus,
    "total_vram": total_vram
}

# Send to scheduler
resp = requests.post(SCHEDULER_URL, json=worker_info)
print(resp.json())