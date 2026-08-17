import os
import socket
import uuid
import shutil
import platform
import GPUtil
import psutil
from config import WORKER_ID_FILE, OUTPUT_DIR

def get_or_create_worker_id() -> str:
    """Retrieve existing worker ID or generate a new persistent one."""
    if os.path.exists(WORKER_ID_FILE):
        with open(WORKER_ID_FILE, "r") as f:
            return f.read().strip()
    
    new_id = str(uuid.uuid4())
    with open(WORKER_ID_FILE, "w") as f:
        f.write(new_id)
    return new_id

def get_hostname() -> str:
    return socket.gethostname()

def get_ip_address() -> str:
    """Resolve the primary non-loopback IP address of this host."""
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            ip = probe.getsockname()[0]
        finally:
            probe.close()
        return ip
    except Exception:
        return "0.0.0.0"

def get_cpu_load() -> float:
    """Current CPU load as a percentage of all cores."""
    try:
        load = os.getloadavg()[0]
        cores = os.cpu_count() or 1
        return round(min(100.0, load / cores * 100.0), 2)
    except Exception:
        return 0.0

def get_mem_usage() -> float:
    """Current system memory usage as a percentage."""
    try:
        with open("/proc/meminfo", "r") as f:
            meminfo = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    meminfo[parts[0].rstrip(":")] = int(parts[1])
        mem_total = meminfo.get("MemTotal", 0)
        mem_available = meminfo.get("MemAvailable", 0)
        if mem_total <= 0:
            return 0.0
        used = (mem_total - mem_available) / mem_total * 100.0
        return round(min(100.0, max(0.0, used)), 2)
    except Exception:
        return 0.0

def get_gpu_info():
    """Retrieve primary GPU specs, VRAM availability, and average GPU load."""
    gpus = GPUtil.getGPUs()
    if not gpus:
        return "Unknown", 0.0, 0.0, 0, 0.0
    
    gpu = gpus[0] if len(gpus) == 1 else max(gpus, key=lambda g: g.memoryTotal)
    gpu_name = gpu.name
    total_vram = round(gpu.memoryTotal / 1024, 2)
    free_vram = round(gpu.memoryFree / 1024, 2)
    num_gpus = len(gpus)
    avg_gpu_load = round(sum(g.load for g in gpus) / num_gpus * 100.0, 2)
    
    return gpu_name, total_vram, free_vram, num_gpus, avg_gpu_load

def count_gpus_in_use() -> int:
    """Number of GPUs currently busy enough to be considered allocated."""
    try:
        gpus = GPUtil.getGPUs()
    except Exception:
        return 0
    busy = 0
    for gpu in gpus:
        if gpu.load > 0.01 or gpu.memoryUsed > 64:
            busy += 1
    return busy

def get_gpus_info() -> list[dict]:
    """Per-GPU specs and live usage for dashboard rendering."""
    try:
        gpus = GPUtil.getGPUs()
    except Exception:
        return []
    result = []
    for index, gpu in enumerate(gpus):
        total = round(gpu.memoryTotal / 1024, 2)
        free = round(gpu.memoryFree / 1024, 2)
        result.append({
            "index": index,
            "name": gpu.name,
            "load": round(gpu.load * 100.0, 2),
            "vramUsedGb": round(max(0.0, total - free), 2),
            "vramFreeGb": free,
            "vramTotalGb": total,
            "temperatureC": round(gpu.temperature, 1) if gpu.temperature else 0.0,
            "inUse": gpu.load > 0.01 or gpu.memoryUsed > 64,
        })
    return result

def get_gpu_temperature() -> float:
    """Current GPU temperature in Celsius (0 if unavailable)."""
    try:
        gpus = GPUtil.getGPUs()
    except Exception:
        return 0.0
    if not gpus:
        return 0.0
    temps = [g.temperature for g in gpus if g.temperature]
    if not temps:
        return 0.0
    return round(sum(temps) / len(temps), 1)

def get_mem_total_gb() -> float:
    try:
        return round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except Exception:
        return 0.0

def get_disk_info() -> tuple[float, float]:
    """Total and free disk in GB on the filesystem hosting job outputs."""
    try:
        usage = shutil.disk_usage(OUTPUT_DIR)
        total = round(usage.total / (1024 ** 3), 1)
        free = round(usage.free / (1024 ** 3), 1)
        return total, free
    except Exception:
        return 0.0, 0.0

def docker_available() -> bool:
    return shutil.which("docker") is not None

def cuda_available() -> bool:
    if shutil.which("nvidia-smi") is not None:
        return True
    try:
        return bool(GPUtil.getGPUs())
    except Exception:
        return False

def get_os_info() -> str:
    try:
        os_name = platform.system()
        os_release = platform.release()
        if os_name == "Linux":
            return f"{os_name} {os_release}"
        if os_name == "Darwin":
            return f"macOS {platform.mac_ver()[0]}"
        return f"{os_name} {os_release}"
    except Exception:
        return platform.system()

def collect_node_info() -> dict:
    """Collect host-level metrics reported to the scheduler."""
    total_disk, free_disk = get_disk_info()
    return {
        "hostname": get_hostname(),
        "ip_address": get_ip_address(),
        "cpu_load": get_cpu_load(),
        "mem_usage": get_mem_usage(),
        "total_disk": total_disk,
        "available_disk": free_disk,
    }