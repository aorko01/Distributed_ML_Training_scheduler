const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

const TOKEN_KEY = 'admin_auth_token';
const USER_KEY = 'admin_auth_user';

export class UnauthorizedError extends Error {
  constructor(message = 'Session expired. Please sign in again.') {
    super(message);
    this.name = 'UnauthorizedError';
  }
}

export const getToken = (): string | null => localStorage.getItem(TOKEN_KEY);

export const getStoredUsername = (): string =>
  localStorage.getItem(USER_KEY) ?? 'admin';

export const isAuthenticated = (): boolean => getToken() !== null;

export interface AuthMe {
  user_id: string;
  username: string;
  email: string;
  name?: string | null;
  is_active: boolean;
  is_superuser: boolean;
}

export const authHeaders = (): Record<string, string> => {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
};

export const login = async (username: string, password: string): Promise<void> => {
  const resp = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: username.trim(), password }),
  });
  if (!resp.ok) {
    let detail = 'Invalid username or password.';
    try {
      const data = (await resp.json()) as { detail?: string };
      if (data.detail) detail = data.detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  const data = (await resp.json()) as { access_token: string };
  localStorage.setItem(TOKEN_KEY, data.access_token);

  // Verify the token belongs to an active admin (superuser). Non-admins are
  // rejected here so the console never renders for unauthorized users.
  try {
    const me = await fetchMe();
    if (!me.is_superuser) {
      logout();
      throw new Error('Admin privileges required.');
    }
    localStorage.setItem(USER_KEY, me.username);
  } catch (err) {
    if (!(err instanceof Error && err.message === 'Admin privileges required.')) {
      logout();
      throw new Error('Could not verify admin session.');
    }
    throw err;
  }
};

export const fetchMe = async (): Promise<AuthMe> => {
  const resp = await fetch(`${API_BASE}/auth/me`, { headers: authHeaders() });
  if (resp.status === 401 || resp.status === 403) {
    logout();
    throw new UnauthorizedError();
  }
  if (!resp.ok) {
    throw new Error(`Failed to fetch session: ${resp.status}`);
  }
  return (await resp.json()) as AuthMe;
};

export const logout = (): void => {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
};

export const handleUnauthorized = (): void => {
  logout();
  if (window.location.pathname !== '/login') {
    window.location.assign('/login');
  }
};
