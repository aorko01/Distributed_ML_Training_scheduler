import React, { useMemo, useState } from 'react';
import { ArrowUp, ArrowDown, Check, X, Zap, Clock, Search } from 'lucide-react';
import {
  queueJobs as seedJobs,
  type PriorityLevel,
  type PriorityRequestStatus,
  type QueueJob,
} from '../data/mock';

type PriorityFilter = 'all' | PriorityLevel;
type RequestFilter = 'all' | 'pending' | 'approved' | 'denied';

const PRIORITY_LABEL: Record<PriorityLevel, string> = {
  high: 'High',
  medium: 'Medium',
  low: 'Low',
};

const getPriorityBadge = (p: PriorityLevel) => (
  <span className={`badge badge-${p === 'high' ? 'failed' : p === 'medium' ? 'pending' : 'offline'}`}>
    {PRIORITY_LABEL[p]}
  </span>
);

const getRequestBadge = (r: PriorityRequestStatus) => {
  if (r === 'none') return null;
  if (r === 'approved') return <span className="badge badge-approved">Approved</span>;
  if (r === 'denied') return <span className="badge badge-denied">Denied</span>;
  return <span className="badge badge-priority-request">Priority Request</span>;
};

const formatRelative = (iso: string) => {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.max(1, Math.round(diffMs / 60000));
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
};

const JobQueue: React.FC = () => {
  const [jobs, setJobs] = useState<QueueJob[]>(seedJobs);
  const [priorityFilter, setPriorityFilter] = useState<PriorityFilter>('all');
  const [requestFilter, setRequestFilter] = useState<RequestFilter>('all');
  const [search, setSearch] = useState('');
  const [actionFeedback, setActionFeedback] = useState<string | null>(null);

  const visibleJobs = useMemo(() => {
    return jobs.filter((job) => {
      if (priorityFilter !== 'all' && job.priority !== priorityFilter) return false;
      if (requestFilter !== 'all' && job.priorityRequest !== requestFilter) return false;
      if (search.trim()) {
        const q = search.toLowerCase();
        if (!`${job.name} ${job.user}`.toLowerCase().includes(q)) return false;
      }
      return true;
    });
  }, [jobs, priorityFilter, requestFilter, search]);

  const showFeedback = (msg: string) => {
    setActionFeedback(msg);
    window.setTimeout(() => setActionFeedback(null), 3000);
  };

  const move = (index: number, dir: -1 | 1) => {
    const target = index + dir;
    if (target < 0 || target >= jobs.length) return;
    setJobs((prev) => {
      const next = [...prev];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
    showFeedback(`Moved ${jobs[index].name} ${dir === -1 ? 'up' : 'down'} in the queue`);
  };

  const decidePriority = (job: QueueJob, decision: 'approved' | 'denied') => {
    setJobs((prev) =>
      prev.map((j) =>
        j.id === job.id
          ? { ...j, priorityRequest: decision, priority: decision === 'approved' ? 'high' : j.priority }
          : j,
      ),
    );
    showFeedback(
      decision === 'approved'
        ? `Priority request approved for ${job.name}`
        : `Priority request denied for ${job.name}`,
    );
  };

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
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
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
              <option value="approved">Approved</option>
              <option value="denied">Denied</option>
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
              <th>Priority</th>
              <th>GPUs</th>
              <th>Submitted</th>
              <th>Priority Request</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {visibleJobs.length === 0 && (
              <tr>
                <td colSpan={8} style={{ textAlign: 'center', color: 'var(--text-secondary)', padding: '2rem' }}>
                  No jobs match the current filters.
                </td>
              </tr>
            )}
            {visibleJobs.map((job) => {
              const realIndex = jobs.findIndex((j) => j.id === job.id);
              return (
                <tr key={job.id}>
                  <td style={{ color: 'var(--text-secondary)', fontVariantNumeric: 'tabular-nums' }}>
                    {realIndex + 1}
                  </td>
                  <td>
                    <div style={{ fontWeight: 600 }}>{job.name}</div>
                    <div className="mono" style={{ color: 'var(--text-secondary)', fontSize: '0.75rem' }}>
                      {job.id}
                    </div>
                  </td>
                  <td>{job.user}</td>
                  <td>{getPriorityBadge(job.priority)}</td>
                  <td>{job.gpuRequested}</td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.375rem' }}>
                      <Clock size={14} color="var(--text-secondary)" />
                      {formatRelative(job.submittedAt)}
                    </span>
                  </td>
                  <td>
                    {getRequestBadge(job.priorityRequest)}
                    {job.priorityRequest === 'pending' && (
                      <div style={{ display: 'flex', gap: '0.375rem', marginTop: '0.375rem' }}>
                        <button
                          className="btn btn-success btn-icon"
                          onClick={() => decidePriority(job, 'approved')}
                          title="Approve priority request"
                        >
                          <Check size={14} />
                        </button>
                        <button
                          className="btn btn-danger btn-icon"
                          onClick={() => decidePriority(job, 'denied')}
                          title="Deny priority request"
                        >
                          <X size={14} />
                        </button>
                      </div>
                    )}
                  </td>
                  <td>
                    <div style={{ display: 'flex', gap: '0.5rem' }}>
                      <button
                        className="btn btn-secondary btn-icon"
                        onClick={() => move(realIndex, -1)}
                        disabled={realIndex === 0}
                        title="Move up"
                      >
                        <ArrowUp size={14} />
                      </button>
                      <button
                        className="btn btn-secondary btn-icon"
                        onClick={() => move(realIndex, 1)}
                        disabled={realIndex === jobs.length - 1}
                        title="Move down"
                      >
                        <ArrowDown size={14} />
                      </button>
                      {job.priorityRequest === 'pending' && (
                        <span style={{ alignSelf: 'center' }} title="Priority escalation requested">
                          <Zap size={14} color="var(--status-building)" />
                        </span>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <p style={{ marginTop: '1rem', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
        Tip: Use the up/down arrows to reorder the queue. Approved priority requests automatically
        promote a job to High priority.
      </p>
    </div>
  );
};

export default JobQueue;
