import requests
import uuid
import GPUtil
import os
import time
from dotenv import load_dotenv

load_dotenv()

# -------------------------
# CONFIG
# -------------------------
BASE_URL = os.getenv("SCHEDULER_URL")

if not BASE_URL:
    raise ValueError("SCHEDULER_URL not set in environment")

BASE_URL = BASE_URL.rstrip("/")  # remove trailing slash if present

REGISTER_URL = f"{BASE_URL}/workers/register"
HEARTBEAT_URL = f"{BASE_URL}/workers/heartbeat"

HEARTBEAT_INTERVAL = 5  # seconds
WORKER_ID_FILE = "worker_id.txt"


# -------------------------
# WORKER ID (persistent)
# -------------------------
if os.path.exists(WORKER_ID_FILE):
    with open(WORKER_ID_FILE, "r") as f:
        worker_id = f.read().strip()
else:
    worker_id = str(uuid.uuid4())
    with open(WORKER_ID_FILE, "w") as f:
        f.write(worker_id)


# -------------------------
# GPU INFO
# -------------------------
def get_gpu_info():
    gpus = GPUtil.getGPUs()
    gpu = gpus[0]  # assume single GPU for now
    gpu_type = gpu.name
    total_vram = round(gpu.memoryTotal / 1024, 2)
    available_vram = round(gpu.memoryFree / 1024, 2)
    num_gpus = len(gpus)
    return gpu_type, total_vram, available_vram, num_gpus


# -------------------------
# MAC ADDRESS
# -------------------------
mac = ':'.join(f'{(uuid.getnode() >> ele) & 0xff:02x}'
               for ele in range(0, 8*6, 8)[::-1])


# -------------------------
# REGISTER WORKER
# -------------------------
def register_worker():
    gpu_type, total_vram, _, num_gpus = get_gpu_info()

    worker_info = {
        "worker_id": worker_id,
        "mac_address": mac,
        "gpu_type": gpu_type,
        "num_gpus": num_gpus,
        "total_vram": total_vram
    }

    resp = requests.post(REGISTER_URL, json=worker_info)
    print("REGISTER RESPONSE:", resp.json())


# -------------------------
# SEND HEARTBEAT
# -------------------------
def send_heartbeat():
    gpu_type, _, available_vram, _ = get_gpu_info()

    heartbeat_payload = {
        "worker_id": worker_id,
        "gpu_type": gpu_type,
        "available_vram": available_vram
    }

    resp = requests.post(HEARTBEAT_URL, json=heartbeat_payload)
    print("HEARTBEAT SENT:", heartbeat_payload)
    print("SERVER RESPONSE:", resp.json())


# -------------------------
# MAIN LOOP
# -------------------------
if __name__ == "__main__":
    register_worker()

    print("Starting heartbeat loop...")
    while True:
        try:
            send_heartbeat()
        except Exception as e:
            print("Heartbeat failed:", e)

        time.sleep(HEARTBEAT_INTERVAL)