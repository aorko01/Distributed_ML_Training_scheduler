import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { fetchJobs, type Job, type JobStatus } from '../services/jobs';
import CopyButton from '../components/CopyButton';
import { Hammer, Loader2, ArrowRight, Package, Rocket } from 'lucide-react';

type StatusFilter = 'All' | JobStatus;
type SortKey = 'newest' | 'oldest' | 'name';

// Build-centric labels for the shared job statuses.
const buildPhase = (status: JobStatus): { label: string; className: string } => {
  switch (status) {
    case 'Building': return { label: 'Building', className: 'badge badge-building' };
    case 'Pending': return { label: 'Queued', className: 'badge badge-pending' };
    case 'ImageReady': return { label: 'Image ready', className: 'badge badge-ready' };
    case 'Estimating': return { label: 'Estimating VRAM', className: 'badge badge-building' };
    case 'Running': return { label: 'Training', className: 'badge badge-running' };
    case 'Completed': return { label: 'Completed', className: 'badge badge-success' };
    case 'Retrying': return { label: 'Retrying', className: 'badge badge-retrying' };
    case 'Failed': return { label: 'Failed', className: 'badge badge-failed' };
    default: return { label: status, className: 'badge badge-pending' };
  }
};

const Builds: React.FC = () => {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('All');
  const [sortBy, setSortBy] = useState<SortKey>('newest');
  const [query, setQuery] = useState('');
  const [copyError, setCopyError] = useState('');
  const navigate = useNavigate();

  useEffect(() => {
    const load = async () => {
      try {
        setJobs(await fetchJobs());
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = jobs.filter((job) => {
      if (statusFilter !== 'All' && job.status !== statusFilter) return false;
      if (q && !`${job.name} ${job.id}`.toLowerCase().includes(q)) return false;
      return true;
    });
    return [...filtered].sort((a, b) => {
      switch (sortBy) {
        case 'oldest': return new Date(a.submittedAt).getTime() - new Date(b.submittedAt).getTime();
        case 'name': return a.name.localeCompare(b.name);
        case 'newest':
        default: return new Date(b.submittedAt).getTime() - new Date(a.submittedAt).getTime();
      }
    });
  }, [jobs, statusFilter, sortBy, query]);

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
        <Loader2 className="animate-spin text-blue-500" size={32} />
      </div>
    );
  }

  return (
    <div className="fade-in builds-page">
      <div className="builds-hero">
        <div>
          <span className="ws-eyebrow"><Hammer size={14} /> Image builds</span>
          <h1>Builds</h1>
          <p>Every workspace image build log. Copy a workspace job id and submit its training from the Training page.</p>
        </div>
        <button className="btn btn-primary" onClick={() => navigate('/submit')}>
          Add Workspace <ArrowRight size={16} />
        </button>
      </div>

      <div className="card builds-toolbar">
        <input
          className="form-input builds-search"
          placeholder="Search workspaces by name or id..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="builds-filters">
          <label>Filter
            <select className="form-select" value={statusFilter} onChange={e => setStatusFilter(e.target.value as StatusFilter)}>
              <option value="All">All statuses</option>
              <option value="Pending">Queued</option>
              <option value="Building">Building</option>
              <option value="ImageReady">Image ready</option>
              <option value="Estimating">Estimating VRAM</option>
              <option value="Running">Training</option>
              <option value="Completed">Completed</option>
              <option value="Retrying">Retrying</option>
              <option value="Failed">Failed</option>
            </select>
          </label>
          <label>Sort
            <select className="form-select" value={sortBy} onChange={e => setSortBy(e.target.value as SortKey)}>
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
              <option value="name">Name (A-Z)</option>
            </select>
          </label>
        </div>
      </div>

      {copyError && <div className="card training-error" role="alert">{copyError}</div>}

      {visible.length === 0 ? (
        <div className="card builds-empty">
          <Hammer size={32} color="var(--accent-primary)" />
          <h3>No workspaces match</h3>
          <p>Try a different search, or add your first workspace.</p>
        </div>
      ) : (
        <div className="builds-grid">
          {visible.map((job) => {
            const phase = buildPhase(job.status);
            return (
              <div
                key={job.id}
                className="card build-card"
                role="button"
                tabIndex={0}
                onClick={() => navigate(`/builds/${job.id}`)}
                onKeyDown={(e) => {
                  // Ignore keys pressed on the buttons inside the card so the
                  // card navigation does not swallow copy/train actions.
                  if (e.target !== e.currentTarget) return;
                  if (e.key === 'Enter' || e.key === ' ') navigate(`/builds/${job.id}`);
                }}
              >
                <div className="build-card-top">
                  <span className="build-card-name">{job.name}</span>
                  <span className={phase.className}>{phase.label}</span>
                </div>
                <div className="build-card-meta">PT {job.pytorchVersion} / CUDA {job.cudaVersion}</div>
                <div className="build-card-id-row">
                  <span className="build-card-meta build-card-id">{job.id}</span>
                  <CopyButton
                    value={job.id}
                    label="Copy id"
                    title={`Copy job id ${job.id}`}
                    className="btn btn-secondary copy-button copy-button--compact"
                    onResult={(ok) => setCopyError(ok ? '' : 'Could not copy the job id; copy it manually.')}
                  />
                </div>
                {job.packages && (
                  <div className="build-card-pkgs"><Package size={13} /> {job.packages.split(/\s+/).filter(Boolean).slice(0, 4).join(', ')}{job.packages.split(/\s+/).filter(Boolean).length > 4 ? ' +' + (job.packages.split(/\s+/).filter(Boolean).length - 4) + ' more' : ''}</div>
                )}
                <div className="build-card-foot">
                  <span>{new Date(job.submittedAt).toLocaleString()}</span>
                  {job.status === 'ImageReady' ? (
                    <button
                      type="button"
                      className="build-card-link build-card-link--button"
                      onClick={(e) => { e.stopPropagation(); navigate(`/training?job=${encodeURIComponent(job.id)}`); }}
                    >
                      Start training <Rocket size={14} />
                    </button>
                  ) : (
                    <span className="build-card-link">Build log <ArrowRight size={14} /></span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default Builds;
