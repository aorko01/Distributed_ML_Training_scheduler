import React from 'react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { LayoutDashboard, PlusCircle, LogOut, Activity, User } from 'lucide-react';
import { logout } from '../services/auth';

const Layout: React.FC = () => {
  const navigate = useNavigate();

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

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
            Submit Job
          </NavLink>
        </nav>
      </aside>

      <main className="main-content">
        <header className="top-header" style={{ display: 'flex', gap: '1.5rem' }}>
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
        </header>
        <div className="page-content">
          <Outlet />
        </div>
      </main>
    </div>
  );
};

export default Layout;
