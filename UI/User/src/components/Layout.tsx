import React from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { LayoutDashboard, PlusCircle, LogOut, Activity, User, Hammer, Rocket, MonitorPlay } from 'lucide-react';
import { logout, getUsername } from '../services/auth';
import { RuntimeWatcher } from '../features/interactive-capacity/RuntimeWatcher';

const Layout: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();

  const handleLogout = () => {
    const user = getUsername();
    if (user) {
      try {
        sessionStorage.removeItem(`interactive-seen-${user}`);
      } catch { /* ignore */ }
    }
    logout();
    navigate('/login');
  };

  // The in-browser code editor is a full workbench: hide the global app
  // sidebar/top-header so it fills the whole page like a real editor.
  const isEditorRoute = /\/interactive\/[^/]+\/editor\/?$/.test(location.pathname);
  if (isEditorRoute) {
    return (
      <div className="app-container app-container--editor fade-in">
        <main className="main-content main-content--editor">
          <div className="page-content page-content--editor">
            <Outlet />
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="app-container fade-in">
      <aside className="sidebar">
        <div className="sidebar-header">
          <Activity className="text-blue-500" size={24} color="var(--accent-primary)" />
          <span>DistributeML</span>
        </div>
        
        <nav className="sidebar-nav">
          <NavLink 
            to="/" 
            end
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          >
            <LayoutDashboard size={20} />
            Dashboard
          </NavLink>
          <NavLink 
            to="/submit" 
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          >
            <PlusCircle size={20} />
            Add Workspace
          </NavLink>
          <NavLink
            to="/builds"
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          >
            <Hammer size={20} />
            Builds
          </NavLink>
          <NavLink
            to="/training"
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          >
            <Rocket size={20} />
            Training
          </NavLink>
          <NavLink to="/interactive" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
            <MonitorPlay size={20} />Interactive workspaces
          </NavLink>
        </nav>
      </aside>

      <main className="main-content">
        <header className="top-header" style={{ display: 'flex', gap: '1.5rem' }}>
          <div className="command-status" aria-label="System console status">
            <span className="command-status-dot" aria-hidden="true" />
            Command Console
          </div>
          <div className="header-actions">
            <NavLink
              to="/profile"
              style={({ isActive }) => ({
                color: isActive ? 'var(--accent-primary)' : 'var(--text-secondary)',
                textDecoration: 'none',
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
                fontWeight: 500,
                fontSize: '0.875rem'
              })}
            >
              <User size={18} />
              Profile
            </NavLink>
            <button
              onClick={handleLogout}
              style={{
                background: 'none', border: 'none', cursor: 'pointer',
                color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: '0.5rem',
                fontWeight: 500, fontSize: '0.875rem'
              }}
            >
              <LogOut size={18} />
              Logout
            </button>
          </div>
        </header>
        <div className="page-content">
          <Outlet />
          {!isEditorRoute && <RuntimeWatcher />}
        </div>
      </main>
    </div>
  );
};

export default Layout;
