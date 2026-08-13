import React from 'react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { ShieldCheck, LayoutDashboard, Server, Users, ListOrdered, LogOut } from 'lucide-react';
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
          <ShieldCheck size={24} color="var(--accent-primary)" />
          <span>Admin Console</span>
        </div>

        <nav className="sidebar-nav">
          <div className="nav-section-label">Cluster</div>
          <NavLink
            to="/"
            end
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          >
            <LayoutDashboard size={20} />
            Overview
          </NavLink>
          <NavLink
            to="/nodes"
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          >
            <Server size={20} />
            Nodes
          </NavLink>

          <div className="nav-section-label">Management</div>
          <NavLink
            to="/users"
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          >
            <Users size={20} />
            Users
          </NavLink>
          <NavLink
            to="/jobs"
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          >
            <ListOrdered size={20} />
            Job Queue
          </NavLink>
        </nav>

        <div className="sidebar-footer">
          <div style={{ marginBottom: '0.5rem' }}>Logged in as</div>
          <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>admin</div>
        </div>
      </aside>

      <main className="main-content">
        <header className="top-header">
          <span style={{ fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
            DistributeML · Cluster Admin
          </span>
          <button
            onClick={handleLogout}
            className="btn btn-secondary btn-sm"
            style={{ color: 'var(--status-failed)', borderColor: 'rgba(239, 68, 68, 0.3)' }}
          >
            <LogOut size={16} />
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
