import React, { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { fetchClusterStats, type ClusterStats } from '../services/stats';
import { fetchJobs, type Job } from '../services/jobs';
import StatusBadge from '../components/StatusBadge';
import { Activity, Clock, Server, PlayCircle, Loader2, History } from 'lucide-react';

type ActiveTrainingStatus = 'Estimating' | 'Running' | 'Retrying';
type StatusFilter = 'All' | ActiveTrainingStatus;
type SortKey = 'newest' | 'oldest' | 'name' | 'gpuHours';

const ACTIVE_TRAINING_STATUSES: ActiveTrainingStatus[] = ['Estimating', 'Running', 'Retrying'];
const REFRESH_INTERVAL_MS = 5_000;

const Dashboard: React.FC = () => {
  const [stats, setStats] = useState<ClusterStats | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('All');
  const [sortBy, setSortBy] = useState<SortKey>('newest');
  const navigate = useNavigate();

  useEffect(() => {
    let active = true;

    const loadInitialData = async () => {
      try {
        const [statsData, jobsData] = await Promise.all([
          fetchClusterStats(),
          fetchJobs(),
        ]);
        if (!active) return;
        setStats(statsData);
        setJobs(jobsData);
      } finally {
        if (active) setLoading(false);
      }
    };

    const refreshJobs = async () => {
      const jobsData = await fetchJobs();
      if (active) setJobs(jobsData);
    };

    void loadInitialData();
    const interval = window.setInterval(() => { void refreshJobs(); }, REFRESH_INTERVAL_MS);

    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, []);

  const activeJobs = useMemo(
    () => jobs.filter(job => (ACTIVE_TRAINING_STATUSES as string[]).includes(job.status)),
    [jobs],
  );

  const visibleJobs = useMemo(() => {
    const filtered = statusFilter === 'All'
      ? activeJobs
      : activeJobs.filter(job => job.status === statusFilter);

    return [...filtered].sort((a, b) => {
      switch (sortBy) {
        case 'oldest': return new Date(a.submittedAt).getTime() - new Date(b.submittedAt).getTime();
        case 'name': return a.name.localeCompare(b.name);
        case 'gpuHours': return b.gpuHours - a.gpuHours;
        case 'newest':
        default: return new Date(b.submittedAt).getTime() - new Date(a.submittedAt).getTime();
      }
    });
  }, [activeJobs, statusFilter, sortBy]);

  const runningCount = activeJobs.filter(job => job.status === 'Running').length;
  const formatDate = (isoString: string) => new Date(isoString).toLocaleString();

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
        <Loader2 className="animate-spin text-blue-500" size={32} />
      </div>
    );
  }

  return (
    <div className="fade-in">
      <div className="jobs-page-heading">
        <div>
          <h1>Dashboard Overview</h1>
          <p>Follow training jobs that are currently active. Statuses refresh automatically.</p>
        </div>
        <Link className="btn btn-secondary jobs-history-link" to="/job-history">
          <History size={17} /> Job History
        </Link>
      </div>

      {stats && (
        <div className="metrics-grid">
          <div className="metric-card">
            <div className="metric-card-heading">
              <div className="metric-title">Queue Length</div>
              <Activity size={20} color="var(--text-secondary)" />
            </div>
            <div className="metric-value">{stats.queueLength}</div>
          </div>
          <div className="metric-card">
            <div className="metric-card-heading">
              <div className="metric-title">GPU Hours Used</div>
              <Clock size={20} color="var(--text-secondary)" />
            </div>
            <div className="metric-value">{stats.gpuHoursUsed.toFixed(1)}</div>
          </div>
          <div className="metric-card">
            <div className="metric-card-heading">
              <div className="metric-title">Training Now</div>
              <PlayCircle size={20} color="var(--text-secondary)" />
            </div>
            <div className="metric-value">{runningCount}</div>
          </div>
          <div className="metric-card">
            <div className="metric-card-heading">
              <div className="metric-title">Total GPUs</div>
              <Server size={20} color="var(--text-secondary)" />
            </div>
            <div className="metric-value">{stats.totalNodes}</div>
          </div>
        </div>
      )}

      <div className="jobs-section-heading">
        <div>
          <h2>Current training jobs</h2>
          <p>VRAM estimation, active training, and jobs waiting to retry.</p>
        </div>
        <div className="jobs-filters">
          <label>
            Status
            <select
              className="form-select"
              value={statusFilter}
              onChange={e => setStatusFilter(e.target.value as StatusFilter)}
            >
              <option value="All">All active</option>
              <option value="Estimating">Estimating VRAM</option>
              <option value="Running">Training</option>
              <option value="Retrying">Retrying</option>
            </select>
          </label>
          <label>
            Sort by
            <select
              className="form-select"
              value={sortBy}
              onChange={e => setSortBy(e.target.value as SortKey)}
            >
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
              <option value="name">Name (A-Z)</option>
              <option value="gpuHours">GPU hours</option>
            </select>
          </label>
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
                <td colSpan={6} className="jobs-empty-cell">
                  {statusFilter === 'All'
                    ? 'No training jobs are active right now. Completed and failed jobs are available in Job History.'
                    : `No jobs currently have the ${statusFilter.toLowerCase()} status.`}
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
                <td><StatusBadge status={job.status} /></td>
                <td>PT {job.pytorchVersion} / CUDA {job.cudaVersion}</td>
                <td><span className="jobs-mono">{job.device}</span></td>
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
