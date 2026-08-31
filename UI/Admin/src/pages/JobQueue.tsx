import React, { useEffect, useMemo, useState } from 'react';
import { Clock, Search } from 'lucide-react';
import {
  queueJobs as seedJobs,
  type PriorityLevel,
  type PriorityRequestStatus,
  type QueueJob,
} from '../data/mock';
import { fetchAdminJobs, type AdminJob } from '../services/api';

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

const toQueueJob = (j: AdminJob): QueueJob => ({
  id: j.id,
  name: j.name || j.id.slice(0, 8),
  user: j.username,
  priority: j.priority === 'HIGH' ? 'high' : j.priority === 'REQUESTED' ? 'medium' : 'low',
  vramRequired: j.vram_required ?? 0,
  submittedAt: j.created_at || new Date().toISOString(),
  priorityRequest: j.priority === 'REQUESTED' ? 'pending' : 'none',
});

const JobQueue: React.FC = () => {
  const [jobs, setJobs] = useState<QueueJob[]>(seedJobs);
  const [priorityFilter, setPriorityFilter] = useState<PriorityFilter>('all');
  const [requestFilter, setRequestFilter] = useState<RequestFilter>('all');
  const [search, setSearch] = useState('');

  useEffect(() => {
    let cancelled = false;
    fetchAdminJobs()
      .then((apiJobs) => {
        if (!cancelled) {
          setJobs(apiJobs.map(toQueueJob));
        }
      })
      .catch(() => {
        if (!cancelled) {
          setJobs(seedJobs);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
      </div>

      <div className="table-container">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Job</th>
              <th>User</th>
              <th>Priority</th>
              <th>VRAM (GB)</th>
              <th>Submitted</th>
              <th>Priority Request</th>
            </tr>
          </thead>
          <tbody>
            {visibleJobs.length === 0 && (
              <tr>
                <td colSpan={7} style={{ textAlign: 'center', color: 'var(--text-secondary)', padding: '2rem' }}>
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
                  <td>{job.vramRequired}</td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.375rem' }}>
                      <Clock size={14} color="var(--text-secondary)" />
                      {formatRelative(job.submittedAt)}
                    </span>
                  </td>
                  <td>
                    {getRequestBadge(job.priorityRequest)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default JobQueue;
