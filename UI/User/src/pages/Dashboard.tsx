import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { fetchClusterStats, type ClusterStats } from '../services/stats';
import { fetchJobs, type Job } from '../services/jobs';
import { Activity, Clock, Server, CheckCircle2, Loader2 } from 'lucide-react';

const Dashboard: React.FC = () => {
  const [stats, setStats] = useState<ClusterStats | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    const loadData = async () => {
      const [statsData, jobsData] = await Promise.all([
        fetchClusterStats(),
        fetchJobs()
      ]);
      setStats(statsData);
      setJobs(jobsData);
      setLoading(false);
    };
    loadData();
  }, []);

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'Pending': return <span className="badge badge-pending">Pending</span>;
      case 'Building': return <span className="badge badge-building">Building</span>;
      case 'Running': return <span className="badge badge-running">Running</span>;
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
              <div className="metric-title">Active Jobs</div>
              <CheckCircle2 size={20} color="var(--text-secondary)" />
            </div>
            <div className="metric-value">{stats.activeJobs}</div>
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

      <h2>Recent Jobs</h2>
      <div className="table-container">
        <table>
          <thead>
            <tr>
              <th>Job ID</th>
              <th>Name</th>
              <th>Status</th>
              <th>Environment</th>
              <th>Submitted At</th>
              <th>GPU Hrs</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map(job => (
              <tr 
                key={job.id} 
                className="job-row"
                onClick={() => navigate(`/jobs/${job.id}`)}
              >
                <td><span style={{ fontFamily: 'monospace' }}>{job.id}</span></td>
                <td style={{ fontWeight: 500 }}>{job.name}</td>
                <td>{getStatusBadge(job.status)}</td>
                <td>PT {job.pytorchVersion} / CUDA {job.cudaVersion}</td>
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
