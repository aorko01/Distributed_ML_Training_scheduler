import GPUtil
import uuid



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

    
    worker_info = {
        "gpu_type": gpu_type,
        "total_vram": total_vram,
        "free_vram": free_vram,
        "num_gpus": num_gpus,
    }
    
    return worker_info

if __name__ == "__main__":
    info = get_worker_info()
    if info:
        for k, v in info.items():
            print(f'"{k}": "{v}"' if isinstance(v, str) else f'"{k}": {v},')