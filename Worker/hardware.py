import os
import socket
import subprocess
import uuid
import shutil
import platform
import time
import GPUtil
import psutil
from config import WORKER_ID_FILE, OUTPUT_DIR


def get_cpu_model() -> str:
    """Return a useful host CPU model without requiring extra packages."""
    try:
        if os.path.exists("/proc/cpuinfo"):
            with open("/proc/cpuinfo", encoding="utf-8") as handle:
                for line in handle:
                    if line.lower().startswith(("model name", "hardware")):
                        value = line.split(":", 1)[-1].strip()
                        if value:
                            return value
    except OSError:
        pass
    return platform.processor() or "Unknown"


def get_uptime_seconds() -> int:
    try:
        return max(0, int(time.time() - psutil.boot_time()))
    except Exception:
        return 0

def get_or_create_worker_id() -> str:
    """Retrieve existing worker ID or generate a new persistent one."""
    if os.path.exists(WORKER_ID_FILE):
        try:
            with open(WORKER_ID_FILE, "r") as f:
                existing = f.read().strip()
            # An empty file (crash during write, manual truncate) must not
            # become worker id "" — that would register as an empty worker
            # and collide with every other empty-id worker.
            if existing:
                return existing
        except OSError:
            pass
    
    new_id = str(uuid.uuid4())
    try:
        os.makedirs(os.path.dirname(os.path.abspath(WORKER_ID_FILE)) or ".", exist_ok=True)
        with open(WORKER_ID_FILE, "w") as f:
            f.write(new_id)
    except OSError:
        pass
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
    """Current CPU load as a percentage of all cores (cross-platform)."""
    try:
        if os.name == "nt":
            # Windows: no getloadavg(); psutil returns percent directly.
            return round(min(100.0, float(psutil.cpu_percent(interval=None))), 2)
        load = os.getloadavg()[0]
        cores = os.cpu_count() or 1
        return round(min(100.0, load / cores * 100.0), 2)
    except Exception:
        return 0.0

def get_cpu_cores() -> int:
    try:
        return os.cpu_count() or 0
    except Exception:
        return 0

def get_mem_usage() -> float:
    """Current system memory usage as a percentage (cross-platform)."""
    try:
        return round(min(100.0, max(0.0, float(psutil.virtual_memory().percent))), 2)
    except Exception:
        pass
    # /proc/meminfo is Linux-only; psutil already covers Windows/macOS above.
    if os.name == "nt" or not os.path.exists("/proc/meminfo"):
        return 0.0
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
    try:
        gpus = GPUtil.getGPUs()
    except Exception:
        return "Unknown", 0.0, 0.0, 0, 0.0
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


def get_docker_data_root() -> str | None:
    """Return Docker's active data root, if the daemon can report it.

    Docker installations may relocate their data directory with ``data-root``
    or a system-specific storage configuration.  Asking the live daemon is
    more reliable than assuming ``/var/lib/docker``.  An explicit environment
    override remains useful for restricted/service environments.
    """
    configured = os.getenv("DOCKER_DATA_ROOT")
    if configured:
        return configured
    docker = shutil.which("docker")
    if not docker:
        return None
    try:
        result = subprocess.run(
            [docker, "info", "--format", "{{.DockerRootDir}}"],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    root = result.stdout.strip()
    return root if root and os.path.isabs(root) else None


def get_disk_info() -> tuple[float, float]:
    """Total and free disk in GB on Docker's active filesystem.

    Fall back to the output directory when Docker is unavailable or its
    reported root cannot be inspected, so telemetry does not become zero just
    because Docker is temporarily stopped.
    """
    storage_path = get_docker_data_root() or OUTPUT_DIR
    try:
        usage = shutil.disk_usage(storage_path)
        total = round(usage.total / (1024 ** 3), 1)
        free = round(usage.free / (1024 ** 3), 1)
        return total, free
    except Exception:
        if storage_path != OUTPUT_DIR:
            try:
                usage = shutil.disk_usage(OUTPUT_DIR)
                return round(usage.total / (1024 ** 3), 1), round(
                    usage.free / (1024 ** 3), 1
                )
            except Exception:
                pass
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
        "cpu_cores": get_cpu_cores(),
        "total_ram": get_mem_total_gb(),
        "total_disk": total_disk,
        "available_disk": free_disk,
    }

def execution_inventory(coordinator,interactive_ready=False,quota_supported=False,available_slots=2):
    """Fresh GPU inventory used for Scheduler assignment decisions.

    Scheduler-owned assignments always reserve a Worker exclusively.  By
    default, desktop/display VRAM and unrelated host GPU activity do not block
    an interactive assignment; the interactive image's requested resources are
    enforced at launch time.  Set INTERACTIVE_REQUIRE_IDLE_GPU=1 to restore the
    previous conservative host-activity gate.
    """
    import time
    import subprocess
    import xml.etree.ElementTree as ET
    complete = False
    gpus = []
    try:
        raw = subprocess.run(['nvidia-smi','-q','-x'],capture_output=True,timeout=3,check=True).stdout
        tree = ET.fromstring(raw)
        require_idle_gpu = os.getenv('INTERACTIVE_REQUIRE_IDLE_GPU', '0').strip() == '1'
        for gpu in tree.findall('gpu'):
            observed_processes = []
            for process in gpu.findall('processes/process_info'):
                observed_processes.append(int(process.findtext('pid')))
            total = float(gpu.findtext('fb_memory_usage/total').split()[0])/1024
            used = float(gpu.findtext('fb_memory_usage/used').split()[0])
            utilization = float(gpu.findtext('utilization/gpu_util').split()[0])
            # Display servers commonly appear in nvidia-smi and consume a small
            # amount of VRAM.  They are not Scheduler assignments and must not
            # hold an otherwise idle Worker in QUEUED state.  Keep the old
            # process/VRAM/utilisation gate as an explicit opt-in for dedicated
            # headless Workers.
            processes = observed_processes if require_idle_gpu else []
            busy = require_idle_gpu and (
                bool(observed_processes)
                or used > float(os.getenv('INTERACTIVE_GPU_BASELINE_MB','64'))
                or utilization > float(os.getenv('INTERACTIVE_GPU_BUSY_PERCENT','1'))
            )
            gpus.append({'uuid':gpu.findtext('uuid'),'model':gpu.findtext('product_name'),'memory_gb':total,
                         'busy':busy,'processes':processes})
        complete = bool(gpus) and all(g['uuid'].startswith('GPU-') for g in gpus)
    except Exception:
        pass
    records = [r for r in coordinator.records() if not r.get('released')]
    _, _, free_vram, _, gpu_load = get_gpu_info()
    node_info = collect_node_info()
    estimation_active = any(r.get('kind') == 'vram_estimation' for r in records)
    try:
        from interactive.docker_ops import developer_mode_enabled, workload_internet_enabled
        from interactive.ssh import ssh_enabled as _ssh_on
        developer_capable = bool(developer_mode_enabled())
        egress_capable = bool(workload_internet_enabled())
        ssh_capable = bool(_ssh_on())
    except Exception:
        developer_capable, egress_capable, ssh_capable = False, False, False
    return {'complete':complete,'observed_at':time.time(),'mode':coordinator.mode,
            'available_slots':0 if estimation_active else (max(0,available_slots-len(records)) if coordinator.mode in ('AVAILABLE','BATCH_ACTIVE') else 0),
            'local_assignments':[r['assignment_id'] for r in records],'free_vram_gb':float(free_vram),
            'free_ram_gb':float(psutil.virtual_memory().available/1024**3),'free_disk_gb':float(node_info['available_disk']),
            'cpu_cores':os.cpu_count() or 0,'platform':'linux/arm64' if platform.machine() == 'aarch64' else 'linux/amd64',
            'nvidia_runtime':interactive_ready,'quota_supported':quota_supported,'interactive_ready':interactive_ready,
            'developer_mode_capable': developer_capable, 'internet_egress_capable': egress_capable,
            'ssh_capable': ssh_capable,
            'gpus':gpus, 'gpu_load': gpu_load,
            'gpus_in_use': count_gpus_in_use(), **node_info}
