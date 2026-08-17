import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { fetchClusterStats, type ClusterStats } from '../services/stats';
import { fetchJobs, type Job, type JobStatus } from '../services/jobs';
import { Activity, Clock, Server, CheckCircle2, Loader2 } from 'lucide-react';

type StatusFilter = 'All' | JobStatus;
type SortKey = 'newest' | 'oldest' | 'name' | 'gpuHours';

const Dashboard: React.FC = () => {
  const [stats, setStats] = useState<ClusterStats | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('All');
  const [sortBy, setSortBy] = useState<SortKey>('newest');
  const navigate = useNavigate();

  useEffect(() => {
    const loadData = async () => {
      try {
        const [statsData, jobsData] = await Promise.all([
          fetchClusterStats(),
          fetchJobs()
        ]);
        setStats(statsData);
        setJobs(jobsData);
      } finally {
        setLoading(false);
      }
    };
    loadData();
  }, []);

  const visibleJobs = useMemo(() => {
    const filtered = statusFilter === 'All'
      ? jobs
      : jobs.filter(job => job.status === statusFilter);

    return [...filtered].sort((a, b) => {
      switch (sortBy) {
        case 'oldest': return new Date(a.submittedAt).getTime() - new Date(b.submittedAt).getTime();
        case 'name': return a.name.localeCompare(b.name);
        case 'gpuHours': return b.gpuHours - a.gpuHours;
        case 'newest':
        default: return new Date(b.submittedAt).getTime() - new Date(a.submittedAt).getTime();
      }
    });
  }, [jobs, statusFilter, sortBy]);

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'Pending': return <span className="badge badge-pending">Pending</span>;
      case 'Building': return <span className="badge badge-building">Building</span>;
      case 'Running': return <span className="badge badge-running">Running</span>;
      case 'Retrying': return <span className="badge badge-retrying">Retrying</span>;
      case 'Completed': return <span className="badge badge-success">Completed</span>;
      case 'Failed': return <span className="badge badge-failed">Failed</span>;
      default: return null;
    }
  };

  const formatDate = (isoString: string) => {
    const d = new Date(isoString);
    return `${d.toLocaleDateString()} ${d.toLocaleTimeString()}`;
  };

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
        <Loader2 className="animate-spin text-blue-500" size={32} />
      </div>
    );
  }

  return (
    <div className="fade-in">
      <h1>Dashboard Overview</h1>
      
      {stats && (
        <div className="metrics-grid">
          <div className="metric-card">
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <div className="metric-title">Queue Length</div>
              <Activity size={20} color="var(--text-secondary)" />
            </div>
            <div className="metric-value">{stats.queueLength}</div>
          </div>
          <div className="metric-card">
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <div className="metric-title">GPU Hours Used</div>
              <Clock size={20} color="var(--text-secondary)" />
            </div>
            <div className="metric-value">{stats.gpuHoursUsed.toFixed(1)}</div>
          </div>
          <div className="metric-card">
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <div className="metric-title">Job Count</div>
              <CheckCircle2 size={20} color="var(--text-secondary)" />
            </div>
            <div className="metric-value">{stats.jobCount}</div>
          </div>
          <div className="metric-card">
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <div className="metric-title">Total Nodes</div>
              <Server size={20} color="var(--text-secondary)" />
            </div>
            <div className="metric-value">{stats.totalNodes}</div>
          </div>
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '1rem', marginBottom: '1rem' }}>
        <h2 style={{ margin: 0 }}>My Jobs</h2>
        <div style={{ display: 'flex', gap: '1rem' }}>
          <div>
            <label className="form-label">Filter</label>
            <select
              className="form-select"
              style={{ width: 'auto', padding: '0.5rem 1rem' }}
              value={statusFilter}
              onChange={e => setStatusFilter(e.target.value as StatusFilter)}
            >
              <option value="All">All Statuses</option>
              <option value="Pending">Pending</option>
              <option value="Building">Building</option>
              <option value="Running">Running</option>
              <option value="Retrying">Retrying</option>
              <option value="Completed">Completed</option>
              <option value="Failed">Failed</option>
            </select>
          </div>
          <div>
            <label className="form-label">Sort By</label>
            <select
              className="form-select"
              style={{ width: 'auto', padding: '0.5rem 1rem' }}
              value={sortBy}
              onChange={e => setSortBy(e.target.value as SortKey)}
            >
              <option value="newest">Newest First</option>
              <option value="oldest">Oldest First</option>
              <option value="name">Name (A-Z)</option>
              <option value="gpuHours">GPU Hours</option>
            </select>
          </div>
        </div>
      </div>

      <div className="table-container">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Status</th>
              <th>Environment</th>
              <th>Device</th>
              <th>Submitted At</th>
              <th>GPU Hrs</th>
            </tr>
          </thead>
          <tbody>
            {visibleJobs.length === 0 && (
              <tr>
                <td colSpan={6} style={{ textAlign: 'center', color: 'var(--text-secondary)' }}>
                  No jobs match the current filters.
                </td>
              </tr>
            )}
            {visibleJobs.map(job => (
              <tr 
                key={job.id} 
                className="job-row"
                onClick={() => navigate(`/jobs/${job.id}`)}
              >
                <td style={{ fontWeight: 500 }}>{job.name}</td>
                <td>{getStatusBadge(job.status)}</td>
                 <td>PT {job.pytorchVersion} / CUDA {job.cudaVersion}</td>
                 <td><span style={{ fontFamily: 'monospace' }}>{job.status === 'Running' || job.status === 'Completed' ? job.device : 'N/A'}</span></td>
                 <td>{formatDate(job.submittedAt)}</td>
                 <td>{job.gpuHours.toFixed(2)}</td>
               </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default Dashboard;
