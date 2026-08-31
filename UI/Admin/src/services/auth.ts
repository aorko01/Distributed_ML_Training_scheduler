const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';
const TOKEN_KEY = 'admin_auth_token';

export const getToken = (): string | null =>
  localStorage.getItem(TOKEN_KEY);

export const isAuthenticated = (): boolean =>
  getToken() !== null;

export const login = async (username: string, password: string): Promise<void> => {
  const resp = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!resp.ok) {
    const data = await resp.json().catch(() => null);
    throw new Error(data?.detail ?? 'Invalid username or password.');
  }
  const data = await resp.json();
  localStorage.setItem(TOKEN_KEY, data.access_token);
};

export const logout = (): void => {
  localStorage.removeItem(TOKEN_KEY);
};
