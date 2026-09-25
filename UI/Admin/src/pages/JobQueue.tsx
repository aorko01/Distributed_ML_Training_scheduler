import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Check, X, Clock, Search } from 'lucide-react';
import {
  fetchJobQueue,
  setJobPriority,
  UnauthorizedError,
  type AdminQueueJob,
} from '../services/api';

type PriorityFilter = 'all' | 'HIGH' | 'NORMAL' | 'REQUESTED';
type RequestFilter = 'all' | 'pending' | 'none';

const PRIORITY_LABEL: Record<string, string> = {
  HIGH: 'High',
  NORMAL: 'Normal',
  REQUESTED: 'Requested',
};

const getStatusBadge = (s: string) => {
  const key = (s ?? '').toUpperCase();
  const className =
    key === 'FAILED'
      ? 'badge-status-failed'
      : key === 'COMPLETED'
        ? 'badge-status-completed'
        : key === 'IN_PROGRESS'
          ? 'badge-status-progress'
          : key === 'RUNNABLE'
            ? 'badge-status-runnable'
            : key === 'IMAGE_BUILDING'
              ? 'badge-status-building'
              : key === 'IMAGE_READY'
                ? 'badge-status-ready'
                : key === 'VRAM_ESTIMATION_PENDING'
                  ? 'badge-status-estimating'
                  : key === 'RETRY_NEEDED'
                    ? 'badge-status-retry'
                    : key === 'NOT_RUNNABLE'
                      ? 'badge-status-neutral'
                      : 'badge-status-neutral';
  const label = key
    ? key
        .toLowerCase()
        .split('_')
        .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
        .join(' ')
    : s;
  return <span className={`badge ${className}`}>{label}</span>;
};

const getPriorityBadge = (p: string) => (
  <span
    className={`badge badge-${
      p === 'HIGH' ? 'priority-high' : p === 'REQUESTED' ? 'priority-requested' : 'priority-normal'
    }`}
  >
    {PRIORITY_LABEL[p] ?? p}
  </span>
);

const formatRelative = (iso: string | null) => {
  if (!iso) return '—';
  const diffMs = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(diffMs)) return '—';
  const mins = Math.max(1, Math.round(diffMs / 60000));
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
};

const REFRESH_INTERVAL_MS = 5000;

const JobQueue: React.FC = () => {
  const [jobs, setJobs] = useState<AdminQueueJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [priorityFilter, setPriorityFilter] = useState<PriorityFilter>('all');
  const [requestFilter, setRequestFilter] = useState<RequestFilter>('all');
  const [search, setSearch] = useState('');
  const [actionFeedback, setActionFeedback] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;

    const refresh = async () => {
      try {
        const queue = await fetchJobQueue();
        if (!cancelled) {
          setJobs(queue);
          setLoadError(null);
          setLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          if (err instanceof UnauthorizedError) {
            navigate('/login', { replace: true });
            return;
          }
          setLoadError(err instanceof Error ? err.message : 'Failed to load job queue.');
          setLoading(false);
        }
      }
    };

    void refresh();
    const interval = window.setInterval(refresh, REFRESH_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [navigate]);

  const visibleJobs = useMemo(() => {
    return jobs.filter((job) => {
      if (priorityFilter !== 'all' && job.priority !== priorityFilter) return false;
      const pending = job.priority === 'REQUESTED';
      if (requestFilter === 'pending' && !pending) return false;
      if (requestFilter === 'none' && pending) return false;
      if (search.trim()) {
        const q = search.toLowerCase();
        if (!`${job.name ?? ''} ${job.username ?? ''} ${job.id}`.toLowerCase().includes(q)) return false;
      }
      return true;
    });
  }, [jobs, priorityFilter, requestFilter, search]);

  const showFeedback = (msg: string) => {
    setActionFeedback(msg);
    window.setTimeout(() => setActionFeedback(null), 3000);
  };

  const decidePriority = async (job: AdminQueueJob, decision: 'HIGH' | 'NORMAL') => {
    try {
      const updated = await setJobPriority(job.id, decision);
      setJobs((prev) => prev.map((j) => (j.id === updated.id ? updated : j)));
      showFeedback(
        decision === 'HIGH'
          ? `Priority approved for ${job.name ?? job.id}`
          : `Priority request denied for ${job.name ?? job.id}`,
      );
    } catch (err) {
      if (err instanceof UnauthorizedError) {
        navigate('/login', { replace: true });
        return;
      }
      showFeedback(err instanceof Error ? err.message : 'Priority update failed.');
    }
  };

  if (loading) {
    return (
      <div className="fade-in">
        <h1>Job Queue</h1>
        <p>Loading job queue…</p>
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="fade-in">
        <h1>Job Queue</h1>
        <p style={{ color: 'var(--status-failed)' }}>{loadError}</p>
      </div>
    );
  }

  return (
    <div className="fade-in">
      <h1>Job Queue</h1>

      <div className="toolbar">
        <div className="toolbar-controls">
          <div className="toolbar-group">
            <label className="form-label">Search</label>
            <div style={{ position: 'relative' }}>
              <Search size={16} style={{ position: 'absolute', left: '0.75rem', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-secondary)' }} />
              <input
                className="form-input"
                style={{ width: 220, paddingLeft: '2.25rem', paddingTop: '0.5rem', paddingBottom: '0.5rem' }}
                placeholder="job name, user..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
          </div>
          <div className="toolbar-group">
            <label className="form-label">Priority</label>
            <select
              className="form-select"
              style={{ width: 'auto', padding: '0.5rem 1rem' }}
              value={priorityFilter}
              onChange={(e) => setPriorityFilter(e.target.value as PriorityFilter)}
            >
              <option value="all">All Priorities</option>
              <option value="HIGH">High</option>
              <option value="NORMAL">Normal</option>
              <option value="REQUESTED">Requested</option>
            </select>
          </div>
          <div className="toolbar-group">
            <label className="form-label">Request Status</label>
            <select
              className="form-select"
              style={{ width: 'auto', padding: '0.5rem 1rem' }}
              value={requestFilter}
              onChange={(e) => setRequestFilter(e.target.value as RequestFilter)}
            >
              <option value="all">All Requests</option>
              <option value="pending">Pending Review</option>
              <option value="none">No Request</option>
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
              <th>#</th>
              <th>Job</th>
              <th>User</th>
              <th>Status</th>
              <th>Priority</th>
              <th>VRAM req.</th>
              <th>Submitted</th>
              <th>Priority Request</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {visibleJobs.length === 0 && (
              <tr>
                <td colSpan={9} style={{ textAlign: 'center', color: 'var(--text-secondary)', padding: '2rem' }}>
                  No jobs match the current filters.
                </td>
              </tr>
            )}
            {visibleJobs.map((job, index) => (
              <tr key={job.id}>
                <td style={{ color: 'var(--text-secondary)', fontVariantNumeric: 'tabular-nums' }}>
                  {index + 1}
                </td>
                <td>
                  <div style={{ fontWeight: 600 }}>{job.name || 'Untitled job'}</div>
                  <div className="mono" style={{ color: 'var(--text-secondary)', fontSize: '0.75rem' }}>
                    {job.id}
                  </div>
                </td>
                <td>{job.username ?? '—'}</td>
                <td>
                  {getStatusBadge(job.status)}
                </td>
                <td>{getPriorityBadge(job.priority)}</td>
                <td>{job.vram_required != null ? `${job.vram_required} GB` : '—'}</td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.375rem' }}>
                    <Clock size={14} color="var(--text-secondary)" />
                    {formatRelative(job.created_at)}
                  </span>
                </td>
                <td>
                  {job.priority === 'REQUESTED' ? (
                    <span>
                      <span className="badge badge-priority-request">Priority Request</span>
                      {job.reason_for_priority && (
                        <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginTop: '0.375rem' }}>
                          {job.reason_for_priority}
                        </div>
                      )}
                    </span>
                  ) : (
                    <span style={{ color: 'var(--text-secondary)' }}>—</span>
                  )}
                </td>
                <td>
                  {job.priority === 'REQUESTED' ? (
                    <div style={{ display: 'flex', gap: '0.375rem' }}>
                      <button
                        className="btn btn-success btn-icon"
                        onClick={() => decidePriority(job, 'HIGH')}
                        title="Approve priority request"
                      >
                        <Check size={14} />
                      </button>
                      <button
                        className="btn btn-danger btn-icon"
                        onClick={() => decidePriority(job, 'NORMAL')}
                        title="Deny priority request"
                      >
                        <X size={14} />
                      </button>
                    </div>
                  ) : (
                    <span style={{ color: 'var(--text-secondary)' }}>—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default JobQueue;
