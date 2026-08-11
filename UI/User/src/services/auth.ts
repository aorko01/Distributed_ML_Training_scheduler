export const isAuthenticated = (): boolean => {
  return localStorage.getItem('auth_token') !== null;
};

// Mock user database
const users: Record<string, any> = {
  admin: { password: 'admin', name: 'Admin User', email: 'admin@distributeml.com' }
};

export const login = (username: string, password: string): Promise<boolean> => {
  return new Promise((resolve) => {
    setTimeout(() => {
      const user = users[username];
      if (user && user.password === password) {
        localStorage.setItem('auth_token', `dummy_token_${username}`);
        localStorage.setItem('username', username);
        resolve(true);
      } else {
        resolve(false);
      }
    }, 500);
  });
};

export const register = (username: string, password: string, name: string, email: string): Promise<boolean> => {
  return new Promise((resolve) => {
    setTimeout(() => {
      if (users[username]) {
        resolve(false); // User exists
      } else {
        users[username] = { password, name, email };
        localStorage.setItem('auth_token', `dummy_token_${username}`);
        localStorage.setItem('username', username);
        resolve(true);
      }
    }, 500);
  });
};

export const logout = () => {
  localStorage.removeItem('auth_token');
  localStorage.removeItem('username');
};

export const getProfile = () => {
  const username = localStorage.getItem('username');
  if (!username) return null;
  const user = users[username];
  if (!user) return null;
  
  // Also load from local storage to persist profile updates during this mock session
  const storedProfile = localStorage.getItem(`profile_${username}`);
  if (storedProfile) {
    return JSON.parse(storedProfile);
  }
  
  return { username, name: user.name, email: user.email };
};

export const updateProfile = (profile: { name: string, email: string }): Promise<boolean> => {
  return new Promise((resolve) => {
    setTimeout(() => {
      const username = localStorage.getItem('username');
      if (username) {
        if (users[username]) {
          users[username].name = profile.name;
          users[username].email = profile.email;
        }
        localStorage.setItem(`profile_${username}`, JSON.stringify({ username, ...profile }));
        resolve(true);
      } else {
        resolve(false);
      }
    }, 400);
  });
};
