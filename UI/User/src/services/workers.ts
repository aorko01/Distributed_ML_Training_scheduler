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
  cpu_cores: number;
  total_ram: number;
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
  cpu_cores?: number | null;
  total_ram?: number | null;
  status: string;
  running_jobs: number;
  total_disk?: number | null;
  available_disk?: number | null;
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
    cpu_cores: node.cpu_cores ?? 0,
    total_ram: node.total_ram ?? 0,
    status: online ? 'online' : 'offline',
    running_jobs: node.running_jobs,
    total_disk: node.total_disk ?? 0,
    available_disk: node.available_disk ?? 0,
    first_seen: node.first_seen,
    last_registered: node.last_registered,
  };
};

const hasError = (body: unknown): body is { error: string } => {
  return body !== null && typeof body === 'object' && 'error' in body;
};

export const fetchAllNodes = async (): Promise<WorkerNode[]> => {
  const data = await api.get<{ nodes?: BackendNode[] } | { error: string }>('/workers/nodes');
  if (hasError(data)) {
    throw new Error(data.error);
  }
  return (data.nodes ?? []).map(mapNode);
};

export interface ResourceOptions {
  gpu_types: string[];
  vram_options: number[];
  ram_options: number[];
  core_options: number[];
  disk_options: number[];
}

export interface ResourceConfig {
  gpu_type?: string;
  gpu_vram?: number;
  cpu_ram?: number;
  cpu_cores?: number;
  disk?: number;
  op: 'ge' | 'eq';
}

export interface ResourceSummary {
  matching_nodes: number;
  avg_running_jobs: number;
  queue_total: number;
  queue_open: number;
}

export interface ResourceRequestPayload {
  gpu_type?: string;
  gpu_vram?: number;
  cpu_ram?: number;
  cpu_cores?: number;
  disk?: number;
}

export interface ResourceRequestResult {
  request_id: string;
  status: string;
  message: string;
  queue_open: number;
}

export const fetchResourceOptions = async (): Promise<ResourceOptions> => {
  const data = await api.get<ResourceOptions>('/resources/options');
  return data;
};

export const fetchResourceSummary = async (config: ResourceConfig): Promise<ResourceSummary> => {
  const data = await api.post<ResourceSummary>('/resources/summary', config);
  return data;
};

export const createResourceRequest = async (
  payload: ResourceRequestPayload,
): Promise<ResourceRequestResult> => {
  return api.post<ResourceRequestResult>('/resources/request', payload);
};