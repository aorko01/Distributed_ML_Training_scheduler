export type JobStatus = 'Running' | 'Queued' | 'Success' | 'Failed' | 'Provisioning';
export type ClusterStatus = 'Online' | 'Degraded' | 'Offline' | 'Registered';

export interface ActiveJob {
  id: string;
  project: string;
  status: JobStatus;
  progress: number;
  queuePos?: number;
  type: string;
  created: string;
  gpu: string;
  owner: string;
  description: string;
  runCommand: string;
  estimatedDuration: string;
}

export interface CompletedJob {
  id: string;
  project: string;
  date: string;
  status: 'Success' | 'Failed';
  duration: string;
  gpu: string;
  score: string;
  artifact: string;
}

export interface ClusterNode {
  name: string;
  status: ClusterStatus;
  gpu: string;
  utilization: number;
  temperature: string;
}

export interface ActivityItem {
  id: string;
  label: string;
  detail: string;
  time: string;
  tone: 'success' | 'warning' | 'info';
}

export interface DashboardOverview {
  activeJobs: number;
  queuedJobs: number;
  runningJobs: number;
  clusterUtilization: number;
  gpuHoursSaved: number;
  successRate: number;
}

export interface DashboardData {
  overview: DashboardOverview;
  activeJobs: ActiveJob[];
  completedJobs: CompletedJob[];
  clusterNodes: ClusterNode[];
  activityFeed: ActivityItem[];
}

export interface SubmissionPayload {
  projectTitle: string;
  description: string;
  runCommand: string;
  vramMode: 'auto' | 'manual';
  vram: number;
  torchVersion: string;
  cudaVersion: string;
  assetFile: File;
}

const configuredApiBaseUrl = (import.meta as any).env?.VITE_API_BASE_URL?.trim();
const API_BASE_URL = (configuredApiBaseUrl || 'http://localhost:8000').replace(/\/$/, '');

async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { Accept: 'application/json' },
  });

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }
  return response.json() as Promise<T>;
}

async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function isApiMode() {
  return true;
}

export async function getDashboardData(): Promise<DashboardData> {
  return apiGet<DashboardData>('/dashboard');
}

export async function submitJob(payload: SubmissionPayload): Promise<DashboardData> {
  const cudaTag = payload.cudaVersion.replace('CUDA ', 'cu').replace('.', '');
  const formData = new FormData();
  formData.append('zip_file', payload.assetFile);
  formData.append('command', payload.runCommand);
  formData.append('docker_base_image', `pytorch/pytorch:${payload.torchVersion}-${cudaTag}-cudnn8-runtime`);
  formData.append('project_title', payload.projectTitle);
  formData.append('description', payload.description);
  formData.append('torch_version', payload.torchVersion);
  formData.append('cuda_version', payload.cudaVersion);
  if (payload.vramMode === 'manual') {
    formData.append('vram_required', String(payload.vram));
  }

  const response = await fetch(`${API_BASE_URL}/jobs/submit_job`, {
    method: 'POST',
    headers: { Accept: 'application/json' },
    body: formData,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(error?.detail || `Request failed with status ${response.status}`);
  }

  return getDashboardData();
}

export async function getJobLogs(jobId: string): Promise<string> {
  const data = await apiPost<{ job_id: string; status: string; content?: string; error?: string }>(
    '/jobs/get_output_by_id',
    { job_id: jobId },
  );
  if (data.error) {
    throw new Error(data.error);
  }
  return data.content ?? '(no output recorded for this job)';
}
