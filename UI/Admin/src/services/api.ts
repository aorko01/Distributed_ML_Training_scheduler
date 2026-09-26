import {
  authHeaders,
  UnauthorizedError,
  handleUnauthorized,
} from './auth';

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
  execution_draining: boolean;
  admin_restricted: boolean;
}

export interface NodesResponse {
  nodes: ApiNode[];
}

export interface ResourceDistribution {
  batch: number;
  experimentation: number;
  idle: number;
}

export interface OverviewStats {
  nodes_online: number;
  nodes_total: number;
  cluster_load: number;
  queue_depth: number;
  gpus_allocated: number;
  gpus_total: number;
  distribution: ResourceDistribution;
}

export interface AdminUser {
  user_id: string;
  username: string;
  email: string;
  name: string | null;
  is_active: boolean;
  is_superuser: boolean;
  jobs_count: number;
  gpu_hours: number;
  created_at: string | null;
}

export interface AdminQueueJob {
  id: string;
  name: string | null;
  username: string | null;
  status: string;
  priority: 'HIGH' | 'NORMAL' | 'REQUESTED' | string;
  vram_required: number | null;
  reason_for_priority: string | null;
  created_at: string | null;
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

export interface WorkerCredential {
  worker_id: string;
  source: 'db' | 'file';
  num_secrets: number;
}

async function authedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const resp = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
      ...(init.headers ?? {}),
    },
  });
  if (resp.status === 401 || resp.status === 403) {
    handleUnauthorized();
    throw new UnauthorizedError();
  }
  return resp;
}

export async function fetchNodes(): Promise<ApiNode[]> {
  const resp = await authedFetch('/workers/nodes');
  if (!resp.ok) {
    throw new Error(`Failed to fetch nodes: ${resp.status}`);
  }
  const data = (await resp.json()) as NodesResponse;
  return data.nodes ?? [];
}

export async function updateWorkerAdmission(workerId: string, restricted: boolean): Promise<void> {
  const resp = await authedFetch(`/admin/workers/${encodeURIComponent(workerId)}/admission`, {
    method: 'PUT',
    body: JSON.stringify({ restricted }),
  });
  if (!resp.ok) {
    throw new Error(`Failed to update worker admission: ${resp.status}`);
  }
}

export async function fetchOverview(): Promise<OverviewStats> {
  const resp = await authedFetch('/scheduler/overview');
  if (!resp.ok) {
    throw new Error(`Failed to fetch overview: ${resp.status}`);
  }
  return (await resp.json()) as OverviewStats;
}

export async function fetchThroughput(): Promise<ThroughputResponse> {
  const resp = await authedFetch('/scheduler/throughput');
  if (!resp.ok) {
    throw new Error(`Failed to fetch throughput: ${resp.status}`);
  }
  return (await resp.json()) as ThroughputResponse;
}

export async function fetchWorkerCredentials(): Promise<WorkerCredential[]> {
  const resp = await authedFetch('/admin/workers/credentials');
  if (!resp.ok) {
    throw new Error(`Failed to fetch worker credentials: ${resp.status}`);
  }
  return (await resp.json()) as WorkerCredential[];
}

export async function registerWorkerCredential(
  worker_id: string,
  secret: string,
): Promise<WorkerCredential> {
  const resp = await authedFetch('/admin/workers/credentials', {
    method: 'POST',
    body: JSON.stringify({ worker_id, secret }),
  });
  if (!resp.ok) {
    let detail = `Failed to register worker: ${resp.status}`;
    try {
      const data = (await resp.json()) as { detail?: string };
      if (data.detail) detail = data.detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return (await resp.json()) as WorkerCredential;
}

export async function fetchAdminUsers(): Promise<AdminUser[]> {
  const resp = await authedFetch('/admin/users');
  if (!resp.ok) {
    throw new Error(`Failed to fetch users: ${resp.status}`);
  }
  return (await resp.json()) as AdminUser[];
}

export async function updateAdminUser(
  user_id: string,
  patch: { is_active?: boolean; is_superuser?: boolean },
): Promise<AdminUser> {
  const resp = await authedFetch(`/admin/users/${encodeURIComponent(user_id)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  });
  if (!resp.ok) {
    let detail = `Failed to update user: ${resp.status}`;
    try {
      const data = (await resp.json()) as { detail?: string };
      if (data.detail) detail = data.detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return (await resp.json()) as AdminUser;
}

export async function deleteAdminUser(user_id: string): Promise<void> {
  const resp = await authedFetch(`/admin/users/${encodeURIComponent(user_id)}`, {
    method: 'DELETE',
  });
  if (!resp.ok && resp.status !== 204) {
    let detail = `Failed to delete user: ${resp.status}`;
    try {
      const data = (await resp.json()) as { detail?: string };
      if (data.detail) detail = data.detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
}

export async function fetchJobQueue(): Promise<AdminQueueJob[]> {
  const resp = await authedFetch('/admin/jobs/queue');
  if (!resp.ok) {
    throw new Error(`Failed to fetch job queue: ${resp.status}`);
  }
  return (await resp.json()) as AdminQueueJob[];
}

export async function setJobPriority(
  job_id: string,
  priority: 'HIGH' | 'NORMAL',
): Promise<AdminQueueJob> {
  const resp = await authedFetch(`/admin/jobs/${encodeURIComponent(job_id)}/priority`, {
    method: 'PATCH',
    body: JSON.stringify({ priority }),
  });
  if (!resp.ok) {
    let detail = `Failed to update priority: ${resp.status}`;
    try {
      const data = (await resp.json()) as { detail?: string };
      if (data.detail) detail = data.detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return (await resp.json()) as AdminQueueJob;
}

export async function revokeWorkerCredential(worker_id: string): Promise<void> {
  const resp = await authedFetch(
    `/admin/workers/credentials/${encodeURIComponent(worker_id)}`,
    { method: 'DELETE' },
  );
  if (!resp.ok && resp.status !== 204) {
    let detail = `Failed to revoke worker: ${resp.status}`;
    try {
      const data = (await resp.json()) as { detail?: string };
      if (data.detail) detail = data.detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
}

export { UnauthorizedError };
