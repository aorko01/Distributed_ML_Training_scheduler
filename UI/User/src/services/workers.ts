import { api } from './api';

export interface WorkerNode {
  worker_id: string;
  hostname: string;
  ip_address: string;
  gpu_type: string;
  num_gpus: number;
  total_vram: number;
  gpus_in_use: number;
  available_vram: number;
  gpu_load: number;
  cpu_load: number;
  mem_usage: number;
  status: 'online' | 'offline';
  running_jobs: number;
  total_disk: number;
  available_disk: number;
  first_seen?: string;
  last_registered?: string;
}

interface BackendNode {
  worker_id: string;
  hostname?: string | null;
  ip_address?: string | null;
  gpu_type: string;
  num_gpus: number;
  total_vram: number;
  gpus_in_use?: number | null;
  available_vram?: number | null;
  gpu_load?: number | null;
  cpu_load?: number | null;
  mem_usage?: number | null;
  status: string;
  running_jobs: number;
  first_seen?: string;
  last_registered?: string;
}

const mapNode = (node: BackendNode): WorkerNode => {
  const online = node.status === 'online';
  return {
    worker_id: node.worker_id,
    hostname: node.hostname ?? node.worker_id,
    ip_address: node.ip_address ?? '-',
    gpu_type: node.gpu_type,
    num_gpus: node.num_gpus,
    total_vram: node.total_vram,
    gpus_in_use: node.gpus_in_use ?? (online ? 0 : node.num_gpus),
    available_vram: node.available_vram ?? node.total_vram,
    gpu_load: node.gpu_load ?? 0,
    cpu_load: node.cpu_load ?? 0,
    mem_usage: node.mem_usage ?? 0,
    status: online ? 'online' : 'offline',
    running_jobs: node.running_jobs,
    total_disk: 0,
    available_disk: 0,
    first_seen: node.first_seen,
    last_registered: node.last_registered,
  };
};

let mockNodes: WorkerNode[] = [
  {
    worker_id: 'worker-9f2c4a1e',
    hostname: 'gpu-a100-01',
    ip_address: '10.0.4.11',
    gpu_type: 'A100 80GB',
    num_gpus: 4,
    total_vram: 320,
    gpus_in_use: 3,
    available_vram: 80,
    gpu_load: 72,
    cpu_load: 45,
    mem_usage: 61,
    status: 'online',
    running_jobs: 3,
    total_disk: 2048,
    available_disk: 1240,
  },
  {
    worker_id: 'worker-7b81d03f',
    hostname: 'gpu-h100-01',
    ip_address: '10.0.4.12',
    gpu_type: 'H100 80GB',
    num_gpus: 8,
    total_vram: 640,
    gpus_in_use: 6,
    available_vram: 160,
    gpu_load: 81,
    cpu_load: 37,
    mem_usage: 58,
    status: 'online',
    running_jobs: 6,
    total_disk: 4096,
    available_disk: 2980,
  },
  {
    worker_id: 'worker-2e55a9c8',
    hostname: 'gpu-l4-01',
    ip_address: '10.0.4.13',
    gpu_type: 'L4 24GB',
    num_gpus: 2,
    total_vram: 48,
    gpus_in_use: 1,
    available_vram: 24,
    gpu_load: 34,
    cpu_load: 22,
    mem_usage: 40,
    status: 'online',
    running_jobs: 1,
    total_disk: 1024,
    available_disk: 810,
  },
  {
    worker_id: 'worker-6cc1f4b7',
    hostname: 'gpu-v100-01',
    ip_address: '10.0.4.14',
    gpu_type: 'V100 16GB',
    num_gpus: 2,
    total_vram: 32,
    gpus_in_use: 0,
    available_vram: 32,
    gpu_load: 0,
    cpu_load: 8,
    mem_usage: 19,
    status: 'online',
    running_jobs: 0,
    total_disk: 512,
    available_disk: 390,
  },
  {
    worker_id: 'worker-8d3a0e26',
    hostname: 'gpu-l4-02',
    ip_address: '10.0.4.15',
    gpu_type: 'L4 24GB',
    num_gpus: 2,
    total_vram: 48,
    gpus_in_use: 2,
    available_vram: 0,
    gpu_load: 96,
    cpu_load: 71,
    mem_usage: 84,
    status: 'online',
    running_jobs: 2,
    total_disk: 1024,
    available_disk: 240,
  },
  {
    worker_id: 'worker-4e19c2d5',
    hostname: 'gpu-a100-02',
    ip_address: '10.0.4.16',
    gpu_type: 'A100 80GB',
    num_gpus: 4,
    total_vram: 320,
    gpus_in_use: 4,
    available_vram: 0,
    gpu_load: 0,
    cpu_load: 0,
    mem_usage: 0,
    status: 'offline',
    running_jobs: 0,
    total_disk: 2048,
    available_disk: 2048,
  },
];

const hasError = (body: unknown): body is { error: string } => {
  return body !== null && typeof body === 'object' && 'error' in body;
};

export const fetchAllNodes = async (): Promise<WorkerNode[]> => {
  try {
    const data = await api.get<{ nodes?: BackendNode[] } | { error: string }>('/workers/nodes');
    if (hasError(data)) {
      throw new Error(data.error);
    }
    const nodes = (data.nodes ?? []).map(mapNode);
    return nodes.length > 0 ? nodes : mockNodes;
  } catch {
    return [...mockNodes];
  }
};

export interface AcquireMachinePayload {
  workerId: string;
  hostname: string;
  gpus: number;
  durationHours: number;
}

export const acquireMachine = async (
  payload: AcquireMachinePayload,
): Promise<{ acquired: boolean; message: string }> => {
  // TODO: Wire up to the scheduler booking endpoint once implemented.
  await new Promise((resolve) => setTimeout(resolve, 900));
  return {
    acquired: true,
    message: `Acquisition request received for ${payload.hostname} (${payload.gpus} GPUs, ${payload.durationHours}h). You will be notified once the reservation is confirmed.`,
  };
};