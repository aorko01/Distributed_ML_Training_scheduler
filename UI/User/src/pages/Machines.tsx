import React, { useEffect, useState } from 'react';
import {
  Terminal,
  Loader2,
  CheckCircle2,
  RotateCcw,
  Box,
  Upload,
} from 'lucide-react';
import {
  fetchJobs,
  submitInteractiveSession,
  submitInteractiveDirect,
  type Job,
  type InteractiveSession,
} from '../services/jobs';

const PYTHON_VERSIONS = ['3.9', '3.10', '3.11', '3.12', '3.13'];
const PYTORCH_VERSIONS = ['2.0.1', '2.1.2', '2.2.2', '2.3.1', '2.4.0', '2.5.1'];
const CUDA_VERSIONS = ['11.8', '12.1', '12.4'];

type Mode = 'existing' | 'direct';

const Interactive: React.FC = () => {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [mode, setMode] = useState<Mode>('existing');

  // Existing-job mode
  const [selectedJobId, setSelectedJobId] = useState<string>('');

  // Direct-upload mode
  const [zipFile, setZipFile] = useState<File | null>(null);
  const [pythonVersion, setPythonVersion] = useState('3.11');
  const [pytorchVersion, setPytorchVersion] = useState('');
  const [cudaVersion, setCudaVersion] = useState('');
  const [customBaseImage, setCustomBaseImage] = useState('');

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
    if (mode === 'existing' && !selectedJobId) return;
    if (mode === 'direct') {
      if (!zipFile) {
        setSubmitError('Please upload a zip archive of your project.');
        return;
      }
      if (cudaVersion && !pytorchVersion) {
        setSubmitError('CUDA version can only be selected together with a PyTorch version.');
        return;
      }
      if (!customBaseImage.trim() && !pythonVersion) {
        setSubmitError('Please pick a Python version (or provide a custom base image).');
        return;
      }
    }

    setSubmitting(true);
    setSubmitError(null);
    setSession(null);
    try {
      if (mode === 'direct') {
        const res = await submitInteractiveDirect(
          {
            name: sessionName,
            pythonVersion: customBaseImage.trim() ? '' : pythonVersion,
            pytorchVersion: customBaseImage.trim() ? undefined : pytorchVersion || undefined,
            cudaVersion: customBaseImage.trim() ? undefined : cudaVersion || undefined,
            baseImage: customBaseImage.trim() || undefined,
          },
          zipFile!,
        );
        setSession(res);
      } else {
        const res = await submitInteractiveSession(selectedJobId, sessionName);
        setSession(res);
      }
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : 'Failed to create interactive session.');
    } finally {
      setSubmitting(false);
    }
  };

  const resetForm = () => {
    setSelectedJobId('');
    setZipFile(null);
    setPythonVersion('3.11');
    setPytorchVersion('');
    setCudaVersion('');
    setCustomBaseImage('');
    setSessionName('');
    setSession(null);
    setSubmitError(null);
  };

  const switchMode = (next: Mode) => {
    setMode(next);
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

  const showEmptyState = mode === 'existing' && jobs.length === 0 && !loadError;

  return (
    <div className="fade-in">
      <h1>Interactive</h1>

      <div className="card demo-banner">
        <Terminal size={18} color="var(--status-pending)" />
        <p style={{ margin: 0 }}>
          Create an interactive SSH + Tailscale sandbox session in two ways: derive it from a job
          you already built, or upload a project directly and pick your environment (Python,
          optionally PyTorch and CUDA). No training code is run — you get a shell in the container.
          You can track the build progress in your dashboard.
        </p>
      </div>

      {loadError && (
        <div className="card demo-banner" style={{ borderColor: 'rgba(239, 68, 68, 0.4)' }}>
          <p className="error-text" style={{ margin: 0 }}>Failed to load data: {loadError}</p>
        </div>
      )}

      <div style={{ display: 'flex', gap: '0.5rem', margin: '0 0 1rem' }}>
        <button
          className={`btn ${mode === 'existing' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => switchMode('existing')}
          style={{ padding: '0.45rem 1rem' }}
        >
          <Box size={15} /> From Existing Job
        </button>
        <button
          className={`btn ${mode === 'direct' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => switchMode('direct')}
          style={{ padding: '0.45rem 1rem' }}
        >
          <Upload size={15} /> Direct Upload
        </button>
      </div>

      {showEmptyState ? (
        <div className="card" style={{ textAlign: 'center', padding: '3rem 1.5rem' }}>
          <Box size={32} color="var(--text-secondary)" style={{ marginBottom: '0.75rem' }} />
          <h3 style={{ marginBottom: '0.5rem' }}>No Jobs Found</h3>
          <p style={{ margin: 0 }}>
            You need at least one job before creating an interactive session from an existing job —
            or switch to Direct Upload above to start one straight from your code.
          </p>
        </div>
      ) : (
        <div className="card" style={{ marginBottom: '1.5rem' }}>
          <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1rem' }}>
            <Terminal size={18} color="var(--text-secondary)" />
            New Interactive Session
          </h3>

          {mode === 'existing' && (
            <>
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
            </>
          )}

          {mode === 'direct' && (
            <>
              <div className="form-group">
                <label className="form-label">Project Archive (.zip)</label>
                <input
                  className="form-select"
                  type="file"
                  accept=".zip"
                  onChange={(e) => {
                    setZipFile(e.target.files?.[0] ?? null);
                    setSession(null);
                    setSubmitError(null);
                  }}
                />
                <p style={{ marginTop: '0.5rem', fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                  Your code is copied into /workspace inside the sandbox; requirements.txt is
                  installed automatically if present.
                </p>
              </div>

              <div className="form-group">
                <label className="form-label">Python Version</label>
                <select
                  className="form-select"
                  value={pythonVersion}
                  disabled={!!customBaseImage.trim()}
                  onChange={(e) => {
                    setPythonVersion(e.target.value);
                    setSession(null);
                    setSubmitError(null);
                  }}
                >
                  {PYTHON_VERSIONS.map((v) => (
                    <option key={v} value={v}>Python {v}</option>
                  ))}
                </select>
              </div>

              <div className="form-group">
                <label className="form-label">PyTorch Version (optional)</label>
                <select
                  className="form-select"
                  value={pytorchVersion}
                  disabled={!!customBaseImage.trim()}
                  onChange={(e) => {
                    setPytorchVersion(e.target.value);
                    if (!e.target.value) setCudaVersion('');
                    setSession(null);
                    setSubmitError(null);
                  }}
                >
                  <option value="">No PyTorch (plain Python image)</option>
                  {PYTORCH_VERSIONS.map((v) => (
                    <option key={v} value={v}>PyTorch {v}</option>
                  ))}
                </select>
              </div>

              <div className="form-group">
                <label className="form-label">CUDA Version (optional, requires PyTorch)</label>
                <select
                  className="form-select"
                  value={cudaVersion}
                  disabled={!pytorchVersion || !!customBaseImage.trim()}
                  onChange={(e) => {
                    setCudaVersion(e.target.value);
                    setSession(null);
                    setSubmitError(null);
                  }}
                >
                  <option value="">
                    {pytorchVersion ? 'CPU-only build' : 'Select PyTorch first...'}
                  </option>
                  {CUDA_VERSIONS.map((v) => (
                    <option key={v} value={v}>CUDA {v}</option>
                  ))}
                </select>
              </div>

              <div className="form-group">
                <label className="form-label">Custom Base Image (optional override)</label>
                <input
                  className="form-select"
                  type="text"
                  placeholder="e.g. nvcr.io/nvidia/pytorch:24.05-py3"
                  value={customBaseImage}
                  onChange={(e) => {
                    setCustomBaseImage(e.target.value);
                    setSession(null);
                    setSubmitError(null);
                  }}
                />
                <p style={{ marginTop: '0.5rem', fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                  If set, this fully overrides the selections above for maximum flexibility.
                </p>
              </div>
            </>
          )}

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
                    An interactive session already exists — reusing it, no rebuild needed.
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
              {mode === 'direct' ? (
                zipFile ? (
                  <>
                    Building a standalone sandbox from{' '}
                    <strong>{zipFile.name}</strong>
                    {customBaseImage.trim()
                      ? <> on <strong>{customBaseImage.trim()}</strong></>
                      : pytorchVersion
                        ? <> with Python <strong>{pythonVersion}</strong>, PyTorch <strong>{pytorchVersion}</strong>{cudaVersion ? `, CUDA ${cudaVersion}` : ' (CPU)'}</>
                        : <> with Python <strong>{pythonVersion}</strong></>}
                  </>
                ) : (
                  <>Upload a zip archive and pick your environment to enable session creation.</>
                )
            ) : selectedJob ? (
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
              disabled={submitting || (mode === 'existing' ? !selectedJobId : !zipFile)}
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
