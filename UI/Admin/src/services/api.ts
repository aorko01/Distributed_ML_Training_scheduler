const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

export interface ApiNode {
  worker_id: string;
  hostname: string | null;
  ip_address: string | null;
  gpu_type: string | null;
  num_gpus: number | null;
  total_vram: number | null;
  available_vram: number | null;
  gpu_load: number | null;
  cpu_load: number | null;
  mem_usage: number | null;
  status: string;
  running_jobs: number | null;
}

export interface NodesResponse {
  nodes: ApiNode[];
}

export async function fetchNodes(): Promise<ApiNode[]> {
  const resp = await fetch(`${API_BASE}/workers/nodes`);
  if (!resp.ok) {
    throw new Error(`Failed to fetch nodes: ${resp.status}`);
  }
  const data = (await resp.json()) as NodesResponse;
  return data.nodes ?? [];
}