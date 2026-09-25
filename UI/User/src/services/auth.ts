import { api, getToken, setToken, clearToken, setUsername, getUsername, clearUsername } from './api';

export interface User {
  user_id: string;
  username: string;
  email: string;
  name: string | null;
  is_active: boolean;
  is_superuser: boolean;
  created_at: string;
}

interface TokenResponse {
  access_token: string;
  token_type: string;
}

export const isAuthenticated = (): boolean => {
  return getToken() !== null;
};

export const isBlockedError = (err: unknown): boolean => {
  if (!err || typeof err !== 'object') return false;
  const status = (err as { status?: unknown }).status;
  const message = err instanceof Error ? err.message : '';
  if (status === 403 && /disabled|blocked|inactive/i.test(message)) return true;
  return /your account has been disabled|account disabled/i.test(message);
};

export const login = async (username: string, password: string): Promise<void> => {
  try {
    const token = await api.post<TokenResponse>('/auth/login', { username, password });
    setToken(token.access_token);
    setUsername(username);
  } catch (err) {
    // Never leave a (possibly stale) token behind when the account is blocked.
    if (isBlockedError(err)) {
      clearToken();
      clearUsername();
      throw new Error('Your account has been blocked. Contact an administrator.');
    }
    throw err;
  }
};

export const register = async (
  username: string,
  password: string,
  name: string,
  email: string,
): Promise<void> => {
  await api.post('/auth/register', { username, email, password, name: name || null });
  const token = await api.post<TokenResponse>('/auth/login', { username, password });
  setToken(token.access_token);
  setUsername(username);
};

export const logout = (): void => {
  clearToken();
  clearUsername();
};

export const getProfile = async (): Promise<User> => {
  return api.get<User>('/auth/me');
};

export const updateProfile = async (profile: { name: string; email: string }): Promise<User> => {
  return api.patch<User>('/auth/me', profile);
};

export { getUsername };
