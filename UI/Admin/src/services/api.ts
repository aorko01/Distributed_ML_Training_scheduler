import { getToken } from './auth';

export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

// --- Types ---

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

export interface AdminUser {
  user_id: string;
  username: string;
  name: string | null;
  email: string;
  is_active: boolean;
  is_superuser: boolean;
  created_at: string | null;
  jobs_count: number;
  gpu_hours: number;
}

export interface AdminJob {
  id: string;
  name: string | null;
  user_id: string;
  username: string;
  priority: string;
  status: string;
  vram_required: number | null;
  reason_for_priority: string | null;
  build_type: string;
  created_at: string | null;
}

export interface CurrentUser {
  user_id: string;
  username: string;
  name: string | null;
  email: string;
  is_active: boolean;
  is_superuser: boolean;
}

// --- Helpers ---

function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
      ...init?.headers,
    },
  });
  if (!resp.ok) {
    throw new Error(`Request failed: ${resp.status}`);
  }
  return resp.json() as Promise<T>;
}

// --- API functions ---

export async function fetchNodes(): Promise<ApiNode[]> {
  const data = await request<NodesResponse>('/workers/nodes');
  return data.nodes ?? [];
}

export async function fetchOverview(): Promise<OverviewStats> {
  return request<OverviewStats>('/scheduler/overview');
}

export async function fetchThroughput(): Promise<ThroughputResponse> {
  return request<ThroughputResponse>('/scheduler/throughput');
}

export async function fetchMe(): Promise<CurrentUser> {
  return request<CurrentUser>('/auth/me');
}

export async function fetchAdminUsers(): Promise<AdminUser[]> {
  return request<AdminUser[]>('/admin/users');
}

export async function fetchAdminJobs(): Promise<AdminJob[]> {
  return request<AdminJob[]>('/admin/jobs');
}
