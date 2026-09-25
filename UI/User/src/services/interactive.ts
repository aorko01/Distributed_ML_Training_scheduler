import { getToken, clearToken, clearUsername } from '../services/api';

export type Creation =
  | { kind: 'upload'; name: string; baseImageId: string; file: File | null; requirements?: ResourceRequirements | null }
  | { kind: 'job'; name: string; sourceJobId: string; requirements?: ResourceRequirements | null };
export interface Revision {
  id: string;
  revision_number: number;
  origin: string;
  state: 'QUEUED' | 'BUILDING' | 'IMAGE_READY' | 'FAILED' | 'CANCELLED';
  image_tag: string | null;
  image_digest_ref: string | null;
  failure_reason: string | null;
  requested_base_image?: string | null;
  source_image_tag?: string | null;
  developer_profile?: string | null;
  package_hint?: string | null;
  ssh_profile?: string | null;
  ssh_hint?: string | null;
}
export interface Workspace {
  id: string;
  name: string;
  source_type: string;
  source_job_id: string | null;
  default_resource_requirements?: ResourceRequirements | null;
  revision: Revision;
}
export interface AssignedMachine {
  display_name: string;
  gpu_model: string | null;
  total_vram_gb: number | null;
  assigned_at: string | null;
}
export interface Runtime {
  id: string; workspace_id: string; revision_id: string; generation: number; profile_version: string;
  state: 'QUEUED' | 'ASSIGNED' | 'PULLING' | 'STARTING' | 'CONNECTING' | 'READY' | 'STOPPING' | 'LOST' | 'STOPPED' | 'FAILED';
  desired_state: 'RUNNING' | 'STOPPED'; failure_detail: string | null; lifetime_deadline: string | null;
  access_service?: 'terminal' | 'workspace'; application_protocol?: 'terminal-stream-v1' | 'workspace-stream-v1'; editor_capable?: boolean;
  ssh_capable?: boolean; ssh_ready?: boolean; ssh_status?: string; ssh_generation?: number | null;
  allow_internet?: boolean;
  developer_mode?: boolean;
  package_capable?: boolean;
  save_enabled?: boolean;
  training_submission_enabled?: boolean;
  requirements?: ResourceRequirements | null;
  assigned_machine?: AssignedMachine | null;
  workspace_name?: string;
}
export interface WorkspaceSave {
  id: string; workspace_id: string; runtime_id: string; generation: number; state: string;
  target_revision_id: string | null; failure_code: string | null; failure_detail: string | null;
}
export interface TrainingSubmission {
  id: string; workspace_id: string; runtime_id: string; state: string; job_id: string | null;
  failure_code: string | null; failure_detail: string | null;
}
export interface ConnectionGrant {
  wss_url: string; ticket: string; expires_at: string; runtime_id: string; generation: number;
  protocol: 'tcp-stream-v1'; terminal_protocol: 'terminal-stream-v1' | null; workspace_protocol?: 'workspace-stream-v1' | null; service?: string;
}
export interface Choice { id: string; label: string }
export interface SourceJob { id: string; name: string; source_kind?: string | null; source_image_label?: string | null }
export interface ResourceRequirements {
  gpu_model?: string | null;
  minimum_vram_gb: number;
  cpu_cores: number;
  memory_gb: number;
  disk_gb: number;
}
export interface CapacityOptions {
  defaults: ResourceRequirements;
  bounds: {
    cpu_cores: { min: number; max: number; default: number };
    memory_gb: { min: number; max: number; default: number };
    disk_gb: { min: number; max: number; default: number };
    minimum_vram_gb: { min: number; max: number; default: number };
    gpu_models_allowlist: string[];
  };
  gpu_models: string[];
  cpu_choices: number[];
  ram_choices: number[];
  vram_choices: number[];
  disk_choices: number[];
}
export interface CapacityWorkload { kind: 'batch_training' | 'vram_estimation' | 'interactive_access'; state: string; mine: boolean }
export interface CapacityMachine {
  machine_key: string;
  display_name: string;
  gpu_model: string | null;
  gpu_count: number;
  total_vram_gb: number;
  free_vram_gb: number;
  cpu_cores: number;
  cpu_load_percent: number | null;
  total_ram_gb: number | null;
  free_ram_gb: number;
  total_disk_gb: number | null;
  free_disk_gb: number;
  gpu_load_percent: number | null;
  available_now: boolean;
  availability_reason: string | null;
  workloads: CapacityWorkload[];
}
export interface CapacityPreview {
  generated_at: string;
  requirements: ResourceRequirements;
  matching_online: number;
  available_now: number;
  busy: number;
  queued_interactive_requests: number;
  machines: CapacityMachine[];
}
const base = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

async function request<T>(path: string, init: RequestInit = {}, prefix = '/interactive/workspaces'): Promise<T> {
  const response = await fetch(base + prefix + path, {
    ...init, headers: { Authorization: `Bearer ${getToken() ?? ''}`, ...init.headers },
  });
  const body = await response.json();
  if (!response.ok) {
    if (response.status === 401) {
      clearToken(); clearUsername();
      window.location.href = '/login';
    }
    const message = typeof body.detail === 'string' ? body.detail : Array.isArray(body.detail) ? body.detail.map((d: { msg?: string }) => d.msg ?? 'Invalid request').join(', ') : `Request failed (${response.status})`;
    throw new Error(message);
  }
  return body as T;
}

export function creationRequest(input: Creation, key: string): { path: string; init: RequestInit } {
  if (input.kind === 'upload') {
    const form = new FormData();
    form.append('name', input.name);
    form.append('base_image_id', input.baseImageId);
    if (input.requirements) form.append('requirements', JSON.stringify(input.requirements));
    if (input.file) form.append('file', input.file);
    return { path: '/from-upload', init: { method: 'POST', headers: { 'Idempotency-Key': key }, body: form } };
  }
  const body: Record<string, unknown> = { name: input.name, source_job_id: input.sourceJobId };
  if (input.requirements) body.requirements = input.requirements;
  return { path: '/from-job', init: { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key },
    body: JSON.stringify(body) } };
}

async function capacityRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(base + '/interactive/capacity' + path, {
    ...init, headers: { Authorization: `Bearer ${getToken() ?? ''}`, ...init.headers },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401) {
      clearToken(); clearUsername();
      window.location.href = '/login';
    }
    const message = typeof (body as { detail?: unknown }).detail === 'string' ? (body as { detail: string }).detail : `Request failed (${response.status})`;
    throw new Error(message);
  }
  return body as T;
}

export const interactiveCapacity = {
  options: (signal?: AbortSignal) => capacityRequest<CapacityOptions>('/options', { signal }),
  preview: (requirements: ResourceRequirements, signal?: AbortSignal) =>
    capacityRequest<CapacityPreview>('/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requirements),
      signal,
    }),
};

export const interactive = {
  runtime: (id: string) => request<Runtime | null>(`/${encodeURIComponent(id)}/runtime`),
  start: (id: string, key: string, requirements?: ResourceRequirements) => request<Runtime>(`/${encodeURIComponent(id)}/runtimes`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key }, body: JSON.stringify(requirements ? { requirements } : {}) }),
  stop: (id: string) => request<Runtime>(`/runtimes/${encodeURIComponent(id)}/stop`, { method: 'POST' }, '/interactive'),
  mine: () => request<Runtime[]>('/runtimes/mine', {}, '/interactive'),
  connection: (id: string, signal?: AbortSignal) => request<ConnectionGrant>(`/runtimes/${encodeURIComponent(id)}/connection`, { method: 'POST', signal }, '/interactive'),
  workspaceConnection: (id: string, signal?: AbortSignal) => request<ConnectionGrant>(`/runtimes/${encodeURIComponent(id)}/workspace-connection`, { method: 'POST', signal }, '/interactive'),
  bases: () => request<Choice[]>('/base-images'),
  sources: () => request<SourceJob[]>('/source-jobs'),
  list: () => request<Workspace[]>(''),
  detail: (id: string) => request<Workspace>(`/${encodeURIComponent(id)}`),
  logs: (id: string) => request<{ lines: string[]; state: string }>(`/${encodeURIComponent(id)}/build-logs`),
  cancel: (id: string) => request<Workspace>(`/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  saveWorkspace: (runtimeId: string, key: string, generation: number, parentRevisionId: string) =>
    request<WorkspaceSave>(`/runtimes/${encodeURIComponent(runtimeId)}/saves`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key },
      body: JSON.stringify({ generation, parent_revision_id: parentRevisionId }),
    }, '/interactive'),
  saveStatus: (saveId: string) => request<WorkspaceSave>(`/saves/${encodeURIComponent(saveId)}`, {}, '/interactive'),
  submitTraining: (runtimeId: string, key: string, generation: number, parentRevisionId: string, settings: { name: string; command: string; resume_command?: string | null; priority?: string }) =>
    request<TrainingSubmission>(`/runtimes/${encodeURIComponent(runtimeId)}/training-submissions`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key },
      body: JSON.stringify({ generation, parent_revision_id: parentRevisionId, settings }),
    }, '/interactive'),
  create: (input: Creation, key: string) => {
    const { path, init } = creationRequest(input, key);
    return request<Workspace>(path, init);
  },
};
