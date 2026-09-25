import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, UserCheck, UserX, Trash2, ShieldAlert } from 'lucide-react';
import {
  deleteAdminUser,
  fetchAdminUsers,
  updateAdminUser,
  UnauthorizedError,
  type AdminUser,
} from '../services/api';

type RoleFilter = 'all' | 'admin' | 'user';
type StatusFilter = 'all' | 'active' | 'disabled';
type SortKey = 'name' | 'role' | 'jobs' | 'gpuHours' | 'created';

const roleOf = (u: AdminUser): 'admin' | 'user' => (u.is_superuser ? 'admin' : 'user');
const statusOf = (u: AdminUser): 'active' | 'disabled' => (u.is_active ? 'active' : 'disabled');

const displayName = (u: AdminUser): string => u.name?.trim() || u.username;

const initialsOf = (u: AdminUser): string => {
  const parts = displayName(u).split(' ').filter(Boolean);
  return parts.map((p) => p[0]).join('').slice(0, 2).toUpperCase() || '?';
};

const createdLabel = (u: AdminUser): string => (u.created_at ?? '').slice(0, 10) || '—';

const Users: React.FC = () => {
  const [userList, setUserList] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [roleFilter, setRoleFilter] = useState<RoleFilter>('all');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [search, setSearch] = useState('');
  const [sortKey, setSortKey] = useState<SortKey>('name');
  const [confirmUser, setConfirmUser] = useState<AdminUser | null>(null);
  const [actionFeedback, setActionFeedback] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const users = await fetchAdminUsers();
        if (!cancelled) {
          setUserList(users);
          setLoadError(null);
        }
      } catch (err) {
        if (!cancelled) {
          if (err instanceof UnauthorizedError) {
            navigate('/login', { replace: true });
            return;
          }
          setLoadError(err instanceof Error ? err.message : 'Failed to load users.');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [navigate]);

  const visibleUsers = useMemo(() => {
    const filtered = userList.filter((u) => {
      if (roleFilter !== 'all' && roleOf(u) !== roleFilter) return false;
      if (statusFilter !== 'all' && statusOf(u) !== statusFilter) return false;
      if (search.trim()) {
        const q = search.toLowerCase();
        if (!`${displayName(u)} ${u.username} ${u.email}`.toLowerCase().includes(q)) return false;
      }
      return true;
    });

    return [...filtered].sort((a, b) => {
      switch (sortKey) {
        case 'role': return roleOf(a).localeCompare(roleOf(b));
        case 'jobs': return b.jobs_count - a.jobs_count;
        case 'gpuHours': return b.gpu_hours - a.gpu_hours;
        case 'created': return (b.created_at ?? '').localeCompare(a.created_at ?? '');
        case 'name':
        default: return displayName(a).localeCompare(displayName(b));
      }
    });
  }, [userList, roleFilter, statusFilter, search, sortKey]);

  const showFeedback = (msg: string) => {
    setActionFeedback(msg);
    window.setTimeout(() => setActionFeedback(null), 3000);
  };

  const mutate = async (user: AdminUser, patch: { is_active?: boolean; is_superuser?: boolean }, verb: string) => {
    try {
      const updated = await updateAdminUser(user.user_id, patch);
      setUserList((prev) => prev.map((u) => (u.user_id === updated.user_id ? updated : u)));
      showFeedback(`${user.username} ${verb}`);
    } catch (err) {
      if (err instanceof UnauthorizedError) {
        navigate('/login', { replace: true });
        return;
      }
      showFeedback(err instanceof Error ? err.message : 'Update failed.');
    }
  };

  const toggleStatus = (user: AdminUser) =>
    mutate(user, { is_active: !user.is_active }, user.is_active ? 'disabled' : 'activated');

  const toggleAdmin = (user: AdminUser) =>
    mutate(
      user,
      { is_superuser: !user.is_superuser },
      user.is_superuser ? 'removed from admins' : 'promoted to admin',
    );

  const removeUser = async (user: AdminUser) => {
    try {
      await deleteAdminUser(user.user_id);
      setUserList((prev) => prev.filter((u) => u.user_id !== user.user_id));
      setConfirmUser(null);
      showFeedback(`${user.username} deleted`);
    } catch (err) {
      if (err instanceof UnauthorizedError) {
        navigate('/login', { replace: true });
        return;
      }
      showFeedback(err instanceof Error ? err.message : 'Delete failed.');
    }
  };

  if (loading) {
    return (
      <div className="fade-in">
        <h1>User Management</h1>
        <p>Loading users…</p>
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="fade-in">
        <h1>User Management</h1>
        <p style={{ color: 'var(--status-failed)' }}>{loadError}</p>
      </div>
    );
  }

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
              onChange={(e) => setSortKey(e.target.value as SortKey)}
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
              <tr key={user.user_id}>
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
                      {initialsOf(user)}
                    </div>
                    <div>
                      <div style={{ fontWeight: 600 }}>{displayName(user)}</div>
                      <div className="mono" style={{ color: 'var(--text-secondary)' }}>@{user.username}</div>
                    </div>
                  </div>
                </td>
                <td>{user.email}</td>
                <td>
                  <span className={`badge badge-role-${roleOf(user)}`}>
                    {roleOf(user) === 'admin' ? 'Admin' : 'User'}
                  </span>
                </td>
                <td>
                  <span className={`badge badge-${statusOf(user)}`}>
                    {statusOf(user) === 'active' ? 'Active' : 'Disabled'}
                  </span>
                </td>
                <td>{user.jobs_count}</td>
                <td>{user.gpu_hours.toFixed(1)}</td>
                <td>{createdLabel(user)}</td>
                <td>
                  <div style={{ display: 'flex', gap: '0.5rem' }}>
                    <button
                      className="btn btn-secondary btn-sm"
                      onClick={() => toggleAdmin(user)}
                      title={user.is_superuser ? 'Remove admin role' : 'Promote to admin'}
                    >
                      <ShieldAlert size={14} />
                      {user.is_superuser ? 'Demote' : 'Make admin'}
                    </button>
                    <button
                      className={`btn btn-sm ${user.is_active ? 'btn-danger' : 'btn-success'}`}
                      onClick={() => toggleStatus(user)}
                    >
                      {user.is_active ? <UserX size={14} /> : <UserCheck size={14} />}
                      {user.is_active ? 'Disable' : 'Enable'}
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
              Are you sure you want to delete <strong>{displayName(confirmUser)}</strong> (@
              {confirmUser.username})? Users owning jobs cannot be deleted. This action
              cannot be undone.
            </p>
            <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'flex-end', marginTop: '1.5rem' }}>
              <button className="btn btn-secondary" onClick={() => setConfirmUser(null)}>
                Cancel
              </button>
              <button className="btn btn-danger" onClick={() => removeUser(confirmUser)}>
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
