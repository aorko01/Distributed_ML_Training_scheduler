import React, { useEffect, useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import {
  fetchJobById,
  fetchJobBuildLogs,
  streamJobBuildLogs,
  type Job,
  type JobStatus,
  type LogLine,
} from '../services/jobs';
import LogTerminal from '../components/LogTerminal';
import { ArrowLeft, Hammer, Loader2, Package } from 'lucide-react';

const BuildDetails: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [job, setJob] = useState<Job | null>(null);
  const [loading, setLoading] = useState(true);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [liveStatus, setLiveStatus] = useState<JobStatus | undefined>(undefined);

  useEffect(() => {
    if (!id) return;
    const loadJob = async () => {
      const data = await fetchJobById(id);
      setJob(data || null);
      setLoading(false);
    };
    loadJob();
  }, [id]);

  useEffect(() => {
    if (!id || !job) return;
    let cancelled = false;
    let stopStream: (() => void) | undefined;

    const isFinished = job.status === 'Completed' || job.status === 'Failed';
    const load = async () => {
      setLogs([]);
      if (isFinished) {
        const stored = await fetchJobBuildLogs(id);
        if (!cancelled) setLogs(stored);
        return;
      }
      stopStream = streamJobBuildLogs(id, {
        onLog: (line) => {
          if (!cancelled) setLogs((prev) => [...prev, line]);
        },
        onDone: (status) => {
          if (cancelled) return;
          if (status === 'COMPLETED') setLiveStatus('Completed');
          else if (status === 'FAILED') setLiveStatus('Failed');
          else if (status === 'RETRY_NEEDED') setLiveStatus('Retrying');
        },
      });
    };
    load();
    return () => {
      cancelled = true;
      stopStream?.();
    };
  }, [id, job]);

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
        <Loader2 className="animate-spin text-blue-500" size={32} />
      </div>
    );
  }

  if (!job) {
    return (
      <div className="fade-in">
        <h2>Workspace not found</h2>
        <button className="btn btn-secondary" onClick={() => navigate('/builds')}>
          Back to Builds
        </button>
      </div>
    );
  }

  return (
    <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ marginBottom: '2rem', display: 'flex', alignItems: 'center', gap: '1rem', flexWrap: 'wrap' }}>
        <button className="btn btn-secondary" style={{ padding: '0.5rem', borderRadius: '50%' }} onClick={() => navigate('/builds')}>
          <ArrowLeft size={20} />
        </button>
        <Hammer size={22} color="var(--accent-primary)" />
        <h1 style={{ margin: 0 }}>Build: {job.name}</h1>
        <span className="badge badge-building">Image build</span>
        <Link to={`/jobs/${job.id}`} className="btn btn-secondary" style={{ marginLeft: 'auto', textDecoration: 'none' }}>
          View training logs
        </Link>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '300px 1fr', gap: '2rem', flex: 1, minHeight: 0 }}>
        <div className="card" style={{ height: 'fit-content' }}>
          <h3>Workspace</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', marginTop: '1rem' }}>
            <div>
              <div style={{ color: 'var(--text-secondary)', fontSize: '0.75rem', textTransform: 'uppercase' }}>ID</div>
              <div style={{ fontFamily: 'monospace', wordBreak: 'break-all' }}>{job.id}</div>
            </div>
            <div>
              <div style={{ color: 'var(--text-secondary)', fontSize: '0.75rem', textTransform: 'uppercase' }}>Environment</div>
              <div>PT {job.pytorchVersion} / CUDA {job.cudaVersion}</div>
            </div>
            {job.packages && (
              <div>
                <div style={{ color: 'var(--text-secondary)', fontSize: '0.75rem', textTransform: 'uppercase' }}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}><Package size={12} /> Packages</span>
                </div>
                <div style={{ fontSize: '0.85rem', wordBreak: 'break-word' }}>{job.packages}</div>
              </div>
            )}
            <div>
              <div style={{ color: 'var(--text-secondary)', fontSize: '0.75rem', textTransform: 'uppercase' }}>Status</div>
              <div>{liveStatus ?? job.status}</div>
            </div>
            <div>
              <div style={{ color: 'var(--text-secondary)', fontSize: '0.75rem', textTransform: 'uppercase' }}>Created</div>
              <div>{new Date(job.submittedAt).toLocaleString()}</div>
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', minHeight: '500px' }}>
          <h3 style={{ marginBottom: '1rem' }}>Build log</h3>
          <LogTerminal logs={logs} jobId={job.id} title={`build — workspace ${job.id}`} />
        </div>
      </div>
    </div>
  );
};

export default BuildDetails;
