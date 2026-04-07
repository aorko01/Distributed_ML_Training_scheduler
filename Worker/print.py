import GPUtil
import uuid

def get_mac_address():
    mac = uuid.getnode()  # returns MAC as integer
    mac_addr = ':'.join(f'{(mac >> ele) & 0xff:02x}' for ele in range(0,8*6,8)[::-1])
    return mac_addr

def get_worker_info():
    gpus = GPUtil.getGPUs()
    if not gpus:
        print("No GPUs found")
        return
    
    gpu = gpus[0]  # take the first GPU for simplicity
    gpu_type = gpu.name
    total_vram = round(gpu.memoryTotal / 1024, 2)  # GB
    free_vram  = round(gpu.memoryFree / 1024, 2)   # GB
    num_gpus   = len(gpus)
    
    mac_addr = get_mac_address()
    
    worker_info = {
        "gpu_type": gpu_type,
        "total_vram": total_vram,
        "free_vram": free_vram,
        "num_gpus": num_gpus,
        "mac_address": mac_addr
    }
    
    return worker_info

if __name__ == "__main__":
    info = get_worker_info()
    if info:
        for k, v in info.items():
            print(f'"{k}": "{v}"' if isinstance(v, str) else f'"{k}": {v},')