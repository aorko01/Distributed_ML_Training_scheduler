import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { fetchClusterStats, type ClusterStats } from '../services/stats';
import { fetchJobs, type Job } from '../services/jobs';
import StatusBadge from '../components/StatusBadge';
import { Activity, Clock, Server, CheckCircle2, Loader2 } from 'lucide-react';

type TrainingStatus = 'Estimating' | 'Running' | 'Retrying' | 'Completed' | 'Failed';
type StatusFilter = 'All' | TrainingStatus;
type SortKey = 'newest' | 'oldest' | 'name' | 'gpuHours';

// Dashboard lists only jobs that reached batch training in some form.
// Build-only phases (Queued / Building / Image ready) live on the Builds page.
const TRAINING_STATUSES: TrainingStatus[] = ['Estimating', 'Running', 'Retrying', 'Completed', 'Failed'];

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
    const trainingOnly = jobs.filter(job => (TRAINING_STATUSES as string[]).includes(job.status));
    const filtered = statusFilter === 'All'
      ? trainingOnly
      : trainingOnly.filter(job => job.status === statusFilter);

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

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '1rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
        <div>
          <h2 style={{ margin: 0 }}>Training jobs</h2>
          <p style={{ margin: '0.25rem 0 0', fontSize: '0.85rem' }}>Estimating, running, retried, completed or failed — builds live on the Builds page.</p>
        </div>
        <div style={{ display: 'flex', gap: '1rem' }}>
          <div>
            <label className="form-label">Filter</label>
            <select
              className="form-select"
              style={{ width: 'auto', padding: '0.5rem 1rem' }}
              value={statusFilter}
              onChange={e => setStatusFilter(e.target.value as StatusFilter)}
            >
              <option value="All">All training</option>
              <option value="Estimating">Estimating VRAM</option>
              <option value="Running">Training</option>
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
                  No training jobs yet — submit training from the Training page once a build is ready.
                </td>
              </tr>
            )}
            {visibleJobs.map(job => (
              <tr
                key={job.id}
                className="job-row"
                onClick={() => navigate(`/jobs/${job.id}`)}
              >
                <td style={{ fontWeight: 500 }}>{job.name}{(job.trainingEligible === false || job.sourceKind === 'PACKAGES_ONLY') && (
                  <span style={{ marginLeft: '0.5rem', fontSize: '0.7rem', color: 'var(--text-secondary)' }}>Interactive only</span>
                )}</td>
                <td><StatusBadge status={job.status} /></td>
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
