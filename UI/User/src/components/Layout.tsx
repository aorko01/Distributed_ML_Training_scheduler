import React, { useEffect, useState } from 'react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { LayoutDashboard, PlusCircle, Terminal, LogOut, User, Boxes, Moon, Sun } from 'lucide-react';
import { logout, getUsername } from '../services/auth';

const Layout: React.FC = () => {
  const navigate = useNavigate();
  const username = getUsername() ?? 'user';

  const [theme, setTheme] = useState<string>(
    () => localStorage.getItem('theme') ?? 'dark'
  );

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
  }, [theme]);

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  return (
    <div className="app-container fade-in">
      <aside className="sidebar">
        <div className="sidebar-header">
          <span className="brand-mark"><Boxes size={18} /></span>
          <div>
            <div className="brand-name">DistributeML</div>
            <div className="brand-sub">Distributed Compute</div>
          </div>
        </div>

        <div className="sidebar-section">Workspace</div>
        <nav className="sidebar-nav">
          <NavLink
            to="/"
            end
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          >
            <LayoutDashboard size={19} />
            Dashboard
          </NavLink>
          <NavLink
            to="/submit"
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          >
            <PlusCircle size={19} />
            Submit Job
          </NavLink>
          <NavLink
            to="/interactive"
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          >
            <Terminal size={19} />
            Interactive
          </NavLink>
        </nav>

        <div className="sidebar-footer">
          <div className="sidebar-user">
            <span className="sidebar-avatar">
              {username.charAt(0).toUpperCase()}
            </span>
            <div className="sidebar-user-meta">
              <div className="sidebar-user-name">{username}</div>
              <div className="sidebar-user-role">Member</div>
            </div>
          </div>
          <NavLink
            to="/profile"
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
            style={{ padding: '0.5rem 0.75rem' }}
          >
            <User size={17} />
            Profile
          </NavLink>
          <button
            onClick={handleLogout}
            className="nav-item"
            style={{ width: '100%', textAlign: 'left' }}
          >
            <LogOut size={17} />
            Logout
          </button>
        </div>
      </aside>

      <main className="main-content">
        <header className="top-header">
          <span className="eyebrow">Distributed ML Training Scheduler</span>
          <button
            type="button"
            className="theme-toggle"
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            aria-label="Toggle color theme"
            title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
          >
            {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
          </button>
        </header>
        <div className="page-content">
          <Outlet />
        </div>
      </main>
    </div>
  );
};

export default Layout;
