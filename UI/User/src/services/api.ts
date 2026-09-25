export const API_BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

/** Scheduler origin for copy-pasteable CLI commands (dml-ssh needs https). */
export function schedulerOrigin(): string {
  try {
    const url = new URL(API_BASE_URL, window.location.origin);
    if (url.protocol === 'https:') return url.origin;
  } catch {
    /* fall through to placeholder */
  }
  return 'https://<scheduler-host>';
}

export const getToken = (): string | null => localStorage.getItem('auth_token');
export const setToken = (token: string): void => localStorage.setItem('auth_token', token);
export const clearToken = (): void => localStorage.removeItem('auth_token');

export const getUsername = (): string | null => localStorage.getItem('username');
export const setUsername = (username: string): void => localStorage.setItem('username', username);
export const clearUsername = (): void => localStorage.removeItem('username');

export interface ApiError extends Error {
  status: number;
}

function isAccountDisabled(body: unknown): boolean {
  if (!body || typeof body !== 'object') return false;
  const detail = (body as Record<string, unknown>).detail;
  return typeof detail === 'string' && /disabled|blocked|inactive/i.test(detail);
}

function extractDetail(body: unknown, fallback: string): string {
  if (!body || typeof body !== 'object') return fallback;
  const detail = (body as Record<string, unknown>).detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d) => (d && typeof d === 'object' && 'msg' in d ? (d as { msg: string }).msg : null))
      .filter((m): m is string => m !== null);
    if (msgs.length > 0) return msgs.join(', ');
  }
  return fallback;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  };

  const token = getToken();
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });

  const contentType = response.headers.get('content-type') ?? '';
  let body: unknown = null;
  if (contentType.includes('application/json')) {
    body = await response.json();
  } else {
    body = await response.text();
  }

  if (!response.ok) {
    const error = new Error(extractDetail(body, `Request failed with status ${response.status}`)) as ApiError;
    error.status = response.status;

    if (response.status === 401 && getToken()) {
      clearToken();
      clearUsername();
      window.location.href = '/login';
    }

    // A user disabled mid-session keeps a syntactically valid token, but
    // guarded routes answer 400/403 ("Inactive user" / "Account disabled").
    // Log them out and send them to the login page with the blocked notice.
    if (getToken() && isAccountDisabled(body)) {
      clearToken();
      clearUsername();
      window.location.href = '/login?blocked=1';
    }

    throw error;
  }

  return body as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, data?: unknown) =>
    request<T>(path, { method: 'POST', body: data !== undefined ? JSON.stringify(data) : undefined }),
  patch: <T>(path: string, data?: unknown) =>
    request<T>(path, { method: 'PATCH', body: data !== undefined ? JSON.stringify(data) : undefined }),
};
