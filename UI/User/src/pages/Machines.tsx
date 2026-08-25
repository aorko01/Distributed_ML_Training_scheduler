import React, { useEffect, useState } from 'react';
import {
  Terminal,
  Loader2,
  CheckCircle2,
  RotateCcw,
  Box,
} from 'lucide-react';
import {
  fetchJobs,
  submitInteractiveSession,
  type Job,
  type InteractiveSession,
} from '../services/jobs';

const Interactive: React.FC = () => {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [selectedJobId, setSelectedJobId] = useState<string>('');
  const [sessionName, setSessionName] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [session, setSession] = useState<InteractiveSession | null>(null);

  useEffect(() => {
    let cancelled = false;
    const loadJobs = async () => {
      try {
        // Only jobs whose image has been built (not NOT_RUNNABLE) can be used
        const data = await fetchJobs(true);
        if (!cancelled) setJobs(data);
      } catch (e) {
        if (!cancelled) setLoadError(e instanceof Error ? e.message : 'Failed to load your jobs.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    loadJobs();
    return () => {
      cancelled = false;
    };
  }, []);

  const selectedJob = jobs.find((j) => j.id === selectedJobId);

  const handleCreate = async () => {
    if (!selectedJobId) return;
    setSubmitting(true);
    setSubmitError(null);
    setSession(null);
    try {
      const res = await submitInteractiveSession(selectedJobId, sessionName);
      setSession(res);
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : 'Failed to create interactive session.');
    } finally {
      setSubmitting(false);
    }
  };

  const resetForm = () => {
    setSelectedJobId('');
    setSessionName('');
    setSession(null);
    setSubmitError(null);
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
      <h1>Interactive</h1>

      <div className="card demo-banner">
        <Terminal size={18} color="var(--status-pending)" />
        <p style={{ margin: 0 }}>
          Pick one of your jobs to create an interactive session on top of it. An SSH + Tailscale
          sandbox image is built from the job's image and pushed to Docker Hub — no training code
          is re-run. You can track the build progress in your dashboard.
        </p>
      </div>

      {loadError && (
        <div className="card demo-banner" style={{ borderColor: 'rgba(239, 68, 68, 0.4)' }}>
          <p className="error-text" style={{ margin: 0 }}>Failed to load data: {loadError}</p>
        </div>
      )}

      {jobs.length === 0 && !loadError ? (
        <div className="card" style={{ textAlign: 'center', padding: '3rem 1.5rem' }}>
          <Box size={32} color="var(--text-secondary)" style={{ marginBottom: '0.75rem' }} />
          <h3 style={{ marginBottom: '0.5rem' }}>No Jobs Found</h3>
          <p style={{ margin: 0 }}>
            You need at least one job before creating an interactive session. Submit a job first.
          </p>
        </div>
      ) : (
        <div className="card" style={{ marginBottom: '1.5rem' }}>
          <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1rem' }}>
            <Terminal size={18} color="var(--text-secondary)" />
            New Interactive Session
          </h3>

          <div className="form-group">
            <label className="form-label">Base Job</label>
            <select
              className="form-select"
              value={selectedJobId}
              onChange={(e) => {
                setSelectedJobId(e.target.value);
                setSession(null);
                setSubmitError(null);
              }}
            >
              <option value="">Select a job...</option>
              {jobs.map((job) => (
                <option key={job.id} value={job.id}>
                  {job.name} · {job.status} · {job.id}
                </option>
              ))}
            </select>
            {selectedJob && (
              <p style={{ marginTop: '0.5rem', fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                A sandbox image will be built on top of this job's environment
                {selectedJob.pytorchVersion !== 'unknown'
                  ? ` (PyTorch ${selectedJob.pytorchVersion}, CUDA ${selectedJob.cudaVersion})`
                  : ''}
                .
              </p>
            )}
          </div>

          <div className="form-group">
            <label className="form-label">Session Name (optional)</label>
            <input
              className="form-select"
              type="text"
              placeholder="e.g. debug-session-1"
              value={sessionName}
              onChange={(e) => setSessionName(e.target.value)}
            />
          </div>

          {(session || submitError) && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginTop: '0.75rem' }}>
              {(session || submitError) && (
                <button
                  className="btn btn-secondary"
                  onClick={resetForm}
                  style={{ padding: '0.4rem 0.9rem' }}
                >
                  <RotateCcw size={14} /> Clear
                </button>
              )}
            </div>
          )}

          {session && (
            <div className="acquire-success" style={{ marginTop: '1rem' }}>
              <CheckCircle2 size={20} color="var(--status-success)" />
              <div>
                {session.status === 'INTERACTIVE_READY' ? (
                  <p style={{ margin: 0 }}>
                    An interactive session for this job already exists — reusing it, no rebuild needed.
                  </p>
                ) : (
                  <p style={{ margin: 0 }}>
                    Interactive session created! Build is running in the background.
                  </p>
                )}
                <p style={{ margin: '0.5rem 0 0', fontSize: '0.85rem', fontFamily: 'monospace' }}>
                  Session ID: {session.id}
                  <br />
                  Image tag: {'<docker-hub-user>'}/{session.id}-interactive:latest
                </p>
              </div>
            </div>
          )}
          {submitError && <p className="error-text" style={{ marginTop: '1rem' }}>{submitError}</p>}

          <div className="config-acquire-bar">
            <div className="config-acquire-info">
              {selectedJob ? (
                <>
                  Creating a session from <strong>{selectedJob.name}</strong>
                </>
              ) : (
                <>Select one of your jobs above to enable session creation.</>
              )}
            </div>
            <button
              className="btn btn-primary"
              onClick={handleCreate}
              disabled={!selectedJobId || submitting}
            >
              {submitting ? <Loader2 className="animate-spin" size={18} /> : <Terminal size={18} />}
              {submitting ? 'Creating...' : 'Create Interactive Session'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

export default Interactive;
