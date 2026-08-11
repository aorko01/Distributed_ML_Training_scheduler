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

export const login = async (username: string, password: string): Promise<void> => {
  const token = await api.post<TokenResponse>('/auth/login', { username, password });
  setToken(token.access_token);
  setUsername(username);
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
