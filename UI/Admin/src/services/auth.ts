const ADMIN_USER = 'admin';
const ADMIN_PASS = 'admin';

const TOKEN_KEY = 'admin_auth_token';

export const isAuthenticated = (): boolean =>
  localStorage.getItem(TOKEN_KEY) !== null;

export const login = async (username: string, password: string): Promise<void> => {
  await new Promise((resolve) => setTimeout(resolve, 500));
  if (username.trim() === ADMIN_USER && password === ADMIN_PASS) {
    localStorage.setItem(TOKEN_KEY, 'dummy-admin-token');
    return;
  }
  throw new Error('Invalid username or password.');
};

export const logout = (): void => {
  localStorage.removeItem(TOKEN_KEY);
};
