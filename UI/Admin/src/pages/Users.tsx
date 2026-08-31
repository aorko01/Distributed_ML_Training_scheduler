import React, { useEffect, useMemo, useState } from 'react';
import { Search } from 'lucide-react';
import {
  users as seedUsers,
  type ManagedUser,
  type UserRole,
  type UserSortKey,
} from '../data/mock';
import { fetchAdminUsers, type AdminUser } from '../services/api';

type RoleFilter = 'all' | UserRole;
type StatusFilter = 'all' | 'active' | 'disabled';

const ROLE_LABEL: Record<UserRole, string> = {
  admin: 'Admin',
  user: 'User',
};

const getRoleBadge = (role: UserRole) => (
  <span className={`badge badge-role-${role}`}>{ROLE_LABEL[role]}</span>
);

const toManagedUser = (u: AdminUser): ManagedUser => ({
  id: u.user_id,
  username: u.username,
  name: u.name || u.username,
  email: u.email,
  role: u.is_superuser ? 'admin' : 'user',
  jobsCount: u.jobs_count,
  gpuHours: u.gpu_hours,
  status: u.is_active ? 'active' : 'disabled',
  created: new Date(u.created_at || Date.now()).toISOString().slice(0, 10),
});

const Users: React.FC = () => {
  const [userList, setUserList] = useState<ManagedUser[]>(seedUsers);
  const [roleFilter, setRoleFilter] = useState<RoleFilter>('all');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [search, setSearch] = useState('');
  const [sortKey, setSortKey] = useState<UserSortKey>('name');

  useEffect(() => {
    let cancelled = false;
    fetchAdminUsers()
      .then((apiUsers) => {
        if (!cancelled) {
          setUserList(apiUsers.map(toManagedUser));
        }
      })
      .catch(() => {
        if (!cancelled) {
          setUserList(seedUsers);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const visibleUsers = useMemo(() => {
    const filtered = userList.filter((u) => {
      if (roleFilter !== 'all' && u.role !== roleFilter) return false;
      if (statusFilter !== 'all' && u.status !== statusFilter) return false;
      if (search.trim()) {
        const q = search.toLowerCase();
        if (!`${u.name} ${u.username} ${u.email}`.toLowerCase().includes(q)) return false;
      }
      return true;
    });

    return [...filtered].sort((a, b) => {
      switch (sortKey) {
        case 'role': return a.role.localeCompare(b.role);
        case 'jobs': return b.jobsCount - a.jobsCount;
        case 'gpuHours': return b.gpuHours - a.gpuHours;
        case 'created': return b.created.localeCompare(a.created);
        case 'name':
        default: return a.name.localeCompare(b.name);
      }
    });
  }, [userList, roleFilter, statusFilter, search, sortKey]);

  return (
    <div className="fade-in">
      <h1>User Management</h1>

      <div className="toolbar">
        <div className="toolbar-controls">
          <div className="toolbar-group">
            <label className="form-label">Search</label>
            <div style={{ position: 'relative' }}>
              <Search size={16} style={{ position: 'absolute', left: '0.75rem', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-secondary)' }} />
              <input
                className="form-input"
                style={{ width: 220, paddingLeft: '2.25rem', paddingTop: '0.5rem', paddingBottom: '0.5rem' }}
                placeholder="name, username, email..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
          </div>
          <div className="toolbar-group">
            <label className="form-label">Role</label>
            <select
              className="form-select"
              style={{ width: 'auto', padding: '0.5rem 1rem' }}
              value={roleFilter}
              onChange={(e) => setRoleFilter(e.target.value as RoleFilter)}
            >
              <option value="all">All Roles</option>
              <option value="admin">Admin</option>
              <option value="user">User</option>
            </select>
          </div>
          <div className="toolbar-group">
            <label className="form-label">Status</label>
            <select
              className="form-select"
              style={{ width: 'auto', padding: '0.5rem 1rem' }}
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
            >
              <option value="all">All Statuses</option>
              <option value="active">Active</option>
              <option value="disabled">Disabled</option>
            </select>
          </div>
          <div className="toolbar-group">
            <label className="form-label">Sort By</label>
            <select
              className="form-select"
              style={{ width: 'auto', padding: '0.5rem 1rem' }}
              value={sortKey}
              onChange={(e) => setSortKey(e.target.value as UserSortKey)}
            >
              <option value="name">Name</option>
              <option value="role">Role</option>
              <option value="jobs">Jobs Count</option>
              <option value="gpuHours">GPU Hours</option>
              <option value="created">Created</option>
            </select>
          </div>
        </div>
      </div>

      <div className="table-container">
        <table>
          <thead>
            <tr>
              <th>User</th>
              <th>Email</th>
              <th>Role</th>
              <th>Status</th>
              <th>Jobs</th>
              <th>GPU Hours</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {visibleUsers.length === 0 && (
              <tr>
                <td colSpan={7} style={{ textAlign: 'center', color: 'var(--text-secondary)', padding: '2rem' }}>
                  No users match the current filters.
                </td>
              </tr>
            )}
            {visibleUsers.map((user) => (
              <tr key={user.id}>
                <td>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                    <div
                      style={{
                        width: 32,
                        height: 32,
                        borderRadius: '50%',
                        backgroundColor: 'var(--bg-tertiary)',
                        border: '1px solid var(--border-color)',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        fontSize: '0.75rem',
                        fontWeight: 600,
                        flexShrink: 0,
                      }}
                    >
                      {user.name.split(' ').map((p) => p[0]).join('').slice(0, 2).toUpperCase()}
                    </div>
                    <div>
                      <div style={{ fontWeight: 600 }}>{user.name}</div>
                      <div className="mono" style={{ color: 'var(--text-secondary)' }}>@{user.username}</div>
                    </div>
                  </div>
                </td>
                <td>{user.email}</td>
                <td>{getRoleBadge(user.role)}</td>
                <td>
                  <span className={`badge badge-${user.status}`}>
                    {user.status === 'active' ? 'Active' : 'Disabled'}
                  </span>
                </td>
                <td>{user.jobsCount}</td>
                <td>{user.gpuHours.toFixed(1)}</td>
                <td>{user.created}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default Users;
