import { getToken, clearToken, clearUsername } from './api';

export type Creation =
  | { kind: 'upload'; name: string; baseImageId: string; file: File }
  | { kind: 'job'; name: string; sourceJobId: string };
export interface Revision {
  id: string;
  revision_number: number;
  origin: string;
  state: 'QUEUED' | 'BUILDING' | 'IMAGE_READY' | 'FAILED' | 'CANCELLED';
  image_tag: string | null;
  image_digest_ref: string | null;
  failure_reason: string | null;
}
export interface Workspace {
  id: string;
  name: string;
  source_type: string;
  source_job_id: string | null;
  revision: Revision;
}
export interface Runtime {
  id: string; workspace_id: string; revision_id: string; generation: number; profile_version: string;
  state: 'QUEUED' | 'ASSIGNED' | 'PULLING' | 'STARTING' | 'CONNECTING' | 'READY' | 'STOPPING' | 'LOST' | 'STOPPED' | 'FAILED';
  desired_state: 'RUNNING' | 'STOPPED'; failure_detail: string | null; lifetime_deadline: string | null;
  access_service?: 'terminal' | 'workspace'; application_protocol?: 'terminal-stream-v1' | 'workspace-stream-v1'; editor_capable?: boolean;
}
export interface ConnectionGrant {
  wss_url: string; ticket: string; expires_at: string; runtime_id: string; generation: number;
  protocol: 'tcp-stream-v1'; terminal_protocol: 'terminal-stream-v1' | null; workspace_protocol?: 'workspace-stream-v1' | null; service?: string;
}
export interface Choice { id: string; label: string }
export interface SourceJob { id: string; name: string }
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
    const message = typeof body.detail === 'string' ? body.detail : `Request failed (${response.status})`;
    throw new Error(message);
  }
  return body as T;
}

export function creationRequest(input: Creation, key: string): { path: string; init: RequestInit } {
  if (input.kind === 'upload') {
    const form = new FormData();
    form.append('name', input.name);
    form.append('base_image_id', input.baseImageId);
    form.append('file', input.file);
    return { path: '/from-upload', init: { method: 'POST', headers: { 'Idempotency-Key': key }, body: form } };
  }
  return { path: '/from-job', init: { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key },
    body: JSON.stringify({ name: input.name, source_job_id: input.sourceJobId }) } };
}

export const interactive = {
  runtime: (id: string) => request<Runtime | null>(`/${encodeURIComponent(id)}/runtime`),
  start: (id: string, key: string) => request<Runtime>(`/${encodeURIComponent(id)}/runtimes`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key }, body: '{}' }),
  stop: (id: string) => request<Runtime>(`/runtimes/${encodeURIComponent(id)}/stop`, { method: 'POST' }, '/interactive'),
  connection: (id: string, signal?: AbortSignal) => request<ConnectionGrant>(`/runtimes/${encodeURIComponent(id)}/connection`, { method: 'POST', signal }, '/interactive'),
  workspaceConnection: (id: string, signal?: AbortSignal) => request<ConnectionGrant>(`/runtimes/${encodeURIComponent(id)}/workspace-connection`, { method: 'POST', signal }, '/interactive'),
  bases: () => request<Choice[]>('/base-images'),
  sources: () => request<SourceJob[]>('/source-jobs'),
  list: () => request<Workspace[]>(''),
  detail: (id: string) => request<Workspace>(`/${encodeURIComponent(id)}`),
  logs: (id: string) => request<{ lines: string[]; state: string }>(`/${encodeURIComponent(id)}/build-logs`),
  cancel: (id: string) => request<Workspace>(`/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  create: (input: Creation, key: string) => {
    const { path, init } = creationRequest(input, key);
    return request<Workspace>(path, init);
  },
};
