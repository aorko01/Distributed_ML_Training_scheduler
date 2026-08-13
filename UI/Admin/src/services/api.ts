const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

export interface ApiNode {
  worker_id: string;
  hostname: string | null;
  ip_address: string | null;
  gpu_type: string | null;
  num_gpus: number | null;
  total_vram: number | null;
  gpus_in_use: number | null;
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

export interface OverviewStats {
  nodes_online: number;
  nodes_total: number;
  cluster_load: number;
  queue_depth: number;
  gpus_allocated: number;
  gpus_total: number;
}

export interface ThroughputPoint {
  label: string;
  jobs: number;
}

export type ThroughputPeriod = 'daily' | 'weekly' | 'monthly' | 'yearly';

export interface ThroughputResponse {
  daily: ThroughputPoint[];
  weekly: ThroughputPoint[];
  monthly: ThroughputPoint[];
  yearly: ThroughputPoint[];
}

export async function fetchNodes(): Promise<ApiNode[]> {
  const resp = await fetch(`${API_BASE}/workers/nodes`);
  if (!resp.ok) {
    throw new Error(`Failed to fetch nodes: ${resp.status}`);
  }
  const data = (await resp.json()) as NodesResponse;
  return data.nodes ?? [];
}

export async function fetchOverview(): Promise<OverviewStats> {
  const resp = await fetch(`${API_BASE}/scheduler/overview`);
  if (!resp.ok) {
    throw new Error(`Failed to fetch overview: ${resp.status}`);
  }
  return (await resp.json()) as OverviewStats;
}

export async function fetchThroughput(): Promise<ThroughputResponse> {
  const resp = await fetch(`${API_BASE}/scheduler/throughput`);
  if (!resp.ok) {
    throw new Error(`Failed to fetch throughput: ${resp.status}`);
  }
  return (await resp.json()) as ThroughputResponse;
}