import React, { useMemo, useState } from 'react';
import { Search, UserCheck, UserX, Trash2, ShieldAlert } from 'lucide-react';
import {
  users as seedUsers,
  type ManagedUser,
  type UserRole,
  type UserSortKey,
} from '../data/mock';

type RoleFilter = 'all' | UserRole;
type StatusFilter = 'all' | 'active' | 'disabled';

const ROLE_LABEL: Record<UserRole, string> = {
  admin: 'Admin',
  researcher: 'Researcher',
  user: 'User',
};

const getRoleBadge = (role: UserRole) => (
  <span className={`badge badge-role-${role}`}>{ROLE_LABEL[role]}</span>
);

const Users: React.FC = () => {
  const [userList, setUserList] = useState<ManagedUser[]>(seedUsers);
  const [roleFilter, setRoleFilter] = useState<RoleFilter>('all');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [search, setSearch] = useState('');
  const [sortKey, setSortKey] = useState<UserSortKey>('name');
  const [confirmUser, setConfirmUser] = useState<ManagedUser | null>(null);
  const [actionFeedback, setActionFeedback] = useState<string | null>(null);

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

  const showFeedback = (msg: string) => {
    setActionFeedback(msg);
    window.setTimeout(() => setActionFeedback(null), 3000);
  };

  const toggleStatus = (user: ManagedUser) => {
    setUserList((prev) =>
      prev.map((u) =>
        u.id === user.id ? { ...u, status: u.status === 'active' ? 'disabled' : 'active' } : u,
      ),
    );
    showFeedback(
      `${user.username} ${user.status === 'active' ? 'disabled' : 'activated'}`,
    );
  };

  const promote = (user: ManagedUser) => {
    setUserList((prev) =>
      prev.map((u) =>
        u.id === user.id
          ? { ...u, role: u.role === 'user' ? 'researcher' : u.role === 'researcher' ? 'admin' : u.role }
          : u,
      ),
    );
    showFeedback(`${user.username} promoted to ${user.role === 'user' ? 'researcher' : 'admin'}`);
  };

  const deleteUser = (user: ManagedUser) => {
    setUserList((prev) => prev.filter((u) => u.id !== user.id));
    setConfirmUser(null);
    showFeedback(`${user.username} deleted`);
  };

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
              <option value="researcher">Researcher</option>
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

        {actionFeedback && (
          <div
            style={{
              fontSize: '0.875rem',
              color: 'var(--accent-primary)',
              backgroundColor: 'rgba(59, 130, 246, 0.1)',
              padding: '0.5rem 1rem',
              borderRadius: 6,
            }}
          >
            {actionFeedback}
          </div>
        )}
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
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {visibleUsers.length === 0 && (
              <tr>
                <td colSpan={8} style={{ textAlign: 'center', color: 'var(--text-secondary)', padding: '2rem' }}>
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
                <td>
                  <div style={{ display: 'flex', gap: '0.5rem' }}>
                    {user.role !== 'admin' && (
                      <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => promote(user)}
                        title="Promote role"
                      >
                        <ShieldAlert size={14} />
                        Promote
                      </button>
                    )}
                    <button
                      className={`btn btn-sm ${user.status === 'active' ? 'btn-danger' : 'btn-success'}`}
                      onClick={() => toggleStatus(user)}
                    >
                      {user.status === 'active' ? <UserX size={14} /> : <UserCheck size={14} />}
                      {user.status === 'active' ? 'Disable' : 'Enable'}
                    </button>
                    <button
                      className="btn btn-secondary btn-sm"
                      onClick={() => setConfirmUser(user)}
                      title="Delete user"
                    >
                      <Trash2 size={14} color="var(--status-failed)" />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {confirmUser && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(0,0,0,0.6)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 50,
          }}
          onClick={() => setConfirmUser(null)}
        >
          <div
            className="card glass"
            style={{ width: '100%', maxWidth: 420 }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ marginTop: 0 }}>Delete user</h3>
            <p>
              Are you sure you want to delete <strong>{confirmUser.name}</strong> (@
              {confirmUser.username})? This action cannot be undone.
            </p>
            <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'flex-end', marginTop: '1.5rem' }}>
              <button className="btn btn-secondary" onClick={() => setConfirmUser(null)}>
                Cancel
              </button>
              <button className="btn btn-danger" onClick={() => deleteUser(confirmUser)}>
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default Users;
