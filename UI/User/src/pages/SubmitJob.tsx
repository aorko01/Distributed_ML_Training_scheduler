import React, { useState, useEffect, useRef, useMemo } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { fetchPytorchVersions, type PytorchVersion, type CudaVariant } from '../services/docker';
import { submitJob } from '../services/jobs';
import {
  UploadCloud, CheckCircle2, Boxes, Cpu, Package, TerminalSquare, FileArchive, Sparkles, X,
} from 'lucide-react';

const parsePackagesPreview = (text: string): string[] =>
  text
    .replace(/,/g, ' ')
    .split(/\s+/)
    .map((t) => t.trim())
    .filter(Boolean)
    .filter((t) => !t.startsWith('#'));

const BatchForm: React.FC = () => {
  const [jobName, setJobName] = useState('');
  const [versions, setVersions] = useState<PytorchVersion[]>([]);
  const [loadingVersions, setLoadingVersions] = useState(true);
  const [versionError, setVersionError] = useState('');
  const [selectedPyTorch, setSelectedPyTorch] = useState('');
  const [selectedCuda, setSelectedCuda] = useState<CudaVariant | null>(null);
  const [packages, setPackages] = useState('');
  const [zipFile, setZipFile] = useState<File | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const [showConfirm, setShowConfirm] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const confirmButtonRef = useRef<HTMLButtonElement>(null);
  const cancelButtonRef = useRef<HTMLButtonElement>(null);
  const lastFocusedRef = useRef<HTMLElement | null>(null);
  const submittingRef = useRef(false);
  const navigate = useNavigate();

  useEffect(() => {
    const loadVersions = async () => {
      setLoadingVersions(true);
      setVersionError('');
      try {
        const data = await fetchPytorchVersions();
        setVersions(data);
        if (data.length > 0) {
          setSelectedPyTorch(data[0].version);
          setSelectedCuda(data[0].cudaVersions[0] ?? null);
        }
      } catch (err) {
        setVersionError(err instanceof Error ? err.message : 'Failed to load PyTorch versions.');
      } finally {
        setLoadingVersions(false);
      }
    };
    loadVersions();
  }, []);

  const handlePyTorchChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const pt = e.target.value;
    setSelectedPyTorch(pt);
    const versionData = versions.find(v => v.version === pt);
    if (versionData && versionData.cudaVersions.length > 0) {
      setSelectedCuda(versionData.cudaVersions[0]);
    } else {
      setSelectedCuda(null);
    }
  };

  const handleFileClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      setZipFile(e.target.files[0]);
      setSubmitError('');
    } else {
      setZipFile(null);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(false);
    const file = e.dataTransfer.files?.[0];
    if (file) {
      if (!file.name.toLowerCase().endsWith('.zip')) {
        setSubmitError('Please attach a .zip archive.');
        return;
      }
      setZipFile(file);
      setSubmitError('');
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!jobName || !selectedPyTorch || !selectedCuda) return;
    if (submittingRef.current || submitting) return;
    if (!zipFile && !showConfirm) {
      lastFocusedRef.current = document.activeElement as HTMLElement | null;
      setShowConfirm(true);
      return;
    }

    await doSubmit();
  };

  const doSubmit = async () => {
    if (submittingRef.current) return;
    const cuda = selectedCuda;
    if (!jobName || !selectedPyTorch || !cuda) return;
    submittingRef.current = true;
    setSubmitting(true);
    setSubmitError('');
    try {
      // Image building and training are decoupled: no entry command here. The
      // job id returned by the backend is used later on the Training page.
      // zipFile may be null: the backend builds a package-only
      // interactive-only image from the base image plus packages.
      const job = await submitJob({
        name: jobName,
        pytorchVersion: selectedPyTorch,
        cudaVersion: cuda.cuda,
        dockerBaseImage: cuda.tag,
        packages: packages.trim() || undefined,
      }, zipFile);
      setShowConfirm(false);
      navigate(`/builds/${job.id}`);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : 'Failed to create workspace.');
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  const handleConfirm = () => {
    void doSubmit();
  };

  const handleCancelConfirm = () => {
    if (submitting) return;
    setShowConfirm(false);
    lastFocusedRef.current?.focus?.();
  };

  useEffect(() => {
    if (!showConfirm) return;
    // Initial focus inside the dialog.
    cancelButtonRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !submittingRef.current) {
        event.preventDefault();
        setShowConfirm(false);
        lastFocusedRef.current?.focus?.();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [showConfirm]);

  const availableCudas = versions.find(v => v.version === selectedPyTorch)?.cudaVersions || [];
  const packagePreview = useMemo(() => parsePackagesPreview(packages), [packages]);

  return (
    <div className="fade-in ws-add">
      <div className="ws-hero">
        <div className="ws-hero-copy">
          <span className="ws-eyebrow"><Sparkles size={14} /> New workspace</span>
          <h1>Add Workspace</h1>
          <p>
            Pick a PyTorch / CUDA base image, drop in your code archive, and list the
            Python packages to install. We only build the image here — no
            <code> requirements.txt </code> and no run command needed inside the zip.
          </p>
          <div className="ws-steps">
            <span className="ws-step"><Boxes size={14} /> 1 · Environment</span>
            <span className="ws-step"><FileArchive size={14} /> 2 · Code</span>
            <span className="ws-step"><Package size={14} /> 3 · Packages</span>
            <span className="ws-step"><TerminalSquare size={14} /> 4 · Built image</span>
          </div>
        </div>
        <div className="ws-hero-card">
          <div className="ws-hero-row"><Cpu size={16} /><span>Base</span><strong>PT {selectedPyTorch || '—'} / CUDA {selectedCuda?.cuda || '—'}</strong></div>
          <div className="ws-hero-row"><FileArchive size={16} /><span>Archive</span><strong>{zipFile ? zipFile.name : 'No archive — interactive-only image'}</strong></div>
          <div className="ws-hero-row"><Package size={16} /><span>Packages</span><strong>{packagePreview.length > 0 ? `${packagePreview.length} listed` : 'None — base image only'}</strong></div>
          <div className="ws-hero-row"><TerminalSquare size={16} /><span>Next</span><strong>{zipFile ? 'Entry command added later on the Training page' : 'Open interactively after build'}</strong></div>
        </div>
      </div>

      <div className="card ws-card">
        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label className="form-label">Workspace name</label>
            <input
              type="text"
              className="form-input"
              placeholder="e.g. ResNet50_Training"
              value={jobName}
              onChange={e => setJobName(e.target.value)}
              required
            />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.5rem' }}>
            <div className="form-group">
              <label className="form-label">PyTorch version</label>
              <select
                className="form-select"
                value={selectedPyTorch}
                onChange={handlePyTorchChange}
                disabled={loadingVersions || versions.length === 0}
              >
                {loadingVersions && <option>Loading versions...</option>}
                {versionError && <option>Failed to load versions</option>}
                {versions.map(v => (
                  <option key={v.version} value={v.version}>{v.version}</option>
                ))}
              </select>
              {versionError && (
                <p style={{ fontSize: '0.75rem', color: 'var(--status-failed)', marginTop: '0.25rem' }}>
                  {versionError}
                </p>
              )}
            </div>
            <div className="form-group">
              <label className="form-label">CUDA / cuDNN version</label>
              <select
                className="form-select"
                value={selectedCuda?.tag ?? ''}
                onChange={e => {
                  const variant = availableCudas.find(v => v.tag === e.target.value);
                  setSelectedCuda(variant ?? null);
                }}
                disabled={loadingVersions || availableCudas.length === 0}
              >
                {loadingVersions && <option>Loading CUDA versions...</option>}
                {!loadingVersions && availableCudas.length === 0 && <option>No CUDA variants</option>}
                {availableCudas.map(c => (
                  <option key={c.tag} value={c.tag}>CUDA {c.cuda} / cuDNN {c.cudnn}</option>
                ))}
              </select>
              {selectedCuda && (
                <p className="ws-hint">Base image: <code>{selectedCuda.tag}</code></p>
              )}
            </div>
          </div>

          <div className="form-group">
            <label className="form-label">Workspace archive (.zip, optional)</label>
            <div
              className={`upload-zone ${dragActive ? 'drag-active' : ''}`}
              onClick={handleFileClick}
              onDragOver={e => { e.preventDefault(); setDragActive(true); }}
              onDragLeave={() => setDragActive(false)}
              onDrop={handleDrop}
            >
              <input
                type="file"
                ref={fileInputRef}
                style={{ display: 'none' }}
                accept=".zip"
                onChange={handleFileChange}
              />
              {zipFile ? (
                <div style={{ color: 'var(--status-success)', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                  <CheckCircle2 size={48} style={{ marginBottom: '1rem' }} />
                  <p style={{ color: 'var(--text-primary)', fontWeight: 500 }}>{zipFile.name}</p>
                  <p style={{ fontSize: '0.875rem' }}>Click to replace file</p>
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                  <UploadCloud size={48} color="var(--accent-primary)" style={{ marginBottom: '1rem' }} />
                  <p style={{ color: 'var(--text-primary)', fontWeight: 500 }}>Click to upload or drag and drop (optional)</p>
                  <p style={{ fontSize: '0.875rem' }}>ZIP with your training scripts and data — no requirements.txt needed. Leave empty for an interactive-only image.</p>
                </div>
              )}
            </div>
            {!zipFile && (
              <p className="ws-hint">No archive — interactive-only image. You can add files later inside the running container/editor.</p>
            )}
          </div>

          <div className="form-group">
            <label className="form-label">Python packages to install</label>
            <textarea
              className="form-textarea ws-packages"
              value={packages}
              onChange={e => setPackages(e.target.value)}
              placeholder={'numpy\npandas==2.0.3\nscikit-learn torchmetrics'}
              spellCheck={false}
            />
            <p className="ws-hint">
              One package per line (or space / comma separated), with optional version pins like
              <code> pandas==2.0.3</code>. Installed with <code>pip install</code> during image build.
              Leave empty to use the base image as-is.
            </p>
            {packagePreview.length > 0 && (
              <div className="ws-chips">
                {packagePreview.map((pkg) => (
                  <span key={pkg} className="ws-chip">
                    <Package size={12} /> {pkg}
                    <button
                      type="button"
                      aria-label={`Remove ${pkg}`}
                      onClick={() => {
                        const remaining = parsePackagesPreview(packages).filter((p) => p !== pkg);
                        setPackages(remaining.join('\n'));
                      }}
                    >
                      <X size={12} />
                    </button>
                  </span>
                ))}
              </div>
            )}
          </div>

          <div className="card ws-next-step">
            <TerminalSquare size={18} />
            <div>
              {zipFile ? (
                <>
                  <strong>Next step: training</strong>
                  <p>
                    This step only builds the image. Once the build finishes, copy the job id
                    from the Builds page and submit the entry and resume commands on the{' '}
                    <Link to="/training">Training page</Link> — VRAM estimation and training
                    start from there.
                  </p>
                </>
              ) : (
                <>
                  <strong>Next step: interactive access</strong>
                  <p>
                    Without an archive this image is interactive-only and cannot be sent
                    directly to training. After the build finishes, open it from the
                    interactive workspace flow and create files in the container/editor.
                  </p>
                </>
              )}
            </div>
          </div>

          {submitError && (
            <div
              style={{
                padding: '0.75rem',
                backgroundColor: 'rgba(239, 68, 68, 0.1)',
                color: 'var(--status-failed)',
                borderRadius: '6px',
                marginTop: '1rem',
                fontSize: '0.875rem',
              }}
            >
              {submitError}
            </div>
          )}

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '1rem', marginTop: '2rem' }}>
            <button type="button" className="btn btn-secondary" onClick={() => navigate(-1)}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={submitting || loadingVersions}>
              {submitting ? 'Creating...' : 'Add Workspace'}
            </button>
          </div>
        </form>
      </div>

      {showConfirm && (
        <div
          className="ws-modal-backdrop"
          style={{
            position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.5)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            zIndex: 1000, padding: '1rem',
          }}
          onClick={handleCancelConfirm}
        >
          <div
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="package-only-title"
            aria-describedby="package-only-desc"
            className="card"
            style={{ maxWidth: 520, width: '100%' }}
            onClick={(e) => e.stopPropagation()}
          >
            <h2 id="package-only-title" style={{ marginTop: 0 }}>Build interactive-only image?</h2>
            <div id="package-only-desc">
              <p>No files will be placed in <code>/workspace</code>.</p>
              {packagePreview.length > 0 ? (
                <p>These packages will still be installed: <code>{packagePreview.join(', ')}</code>.</p>
              ) : (
                <p>No packages listed — the base image will be used as-is.</p>
              )}
              <p>This image cannot be sent directly to training.</p>
              <p>It can be opened interactively so files can be created in the container/editor.</p>
            </div>
            {submitError && (
              <div role="alert" style={{ padding: '0.75rem', backgroundColor: 'rgba(239, 68, 68, 0.1)', color: 'var(--status-failed)', borderRadius: '6px', marginTop: '1rem', fontSize: '0.875rem' }}>
                {submitError}
              </div>
            )}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.75rem', marginTop: '1.5rem' }}>
              <button ref={cancelButtonRef} type="button" className="btn btn-secondary" onClick={handleCancelConfirm} disabled={submitting}>
                Go back
              </button>
              <button ref={confirmButtonRef} type="button" className="btn btn-primary" onClick={handleConfirm} disabled={submitting}>
                {submitting ? 'Building...' : 'Build interactive-only image'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

const SubmitJob: React.FC = () => {
  // Every workspace — batch or interactive — starts with an image build,
  // so this page is build-only with no job-type selection.
  return <BatchForm />;
};
export default SubmitJob;
