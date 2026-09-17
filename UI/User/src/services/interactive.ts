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
export interface Choice { id: string; label: string }
export interface SourceJob { id: string; name: string }
const base = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(base + '/interactive/workspaces' + path, {
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
