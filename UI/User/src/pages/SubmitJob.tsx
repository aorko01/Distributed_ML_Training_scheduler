import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { fetchPytorchVersions, type PytorchVersion, type CudaVariant } from '../services/docker';
import { submitJob } from '../services/jobs';
import { UploadCloud, CheckCircle2, AlertTriangle } from 'lucide-react';

const SubmitJob: React.FC = () => {
  const [jobName, setJobName] = useState('');
  const [versions, setVersions] = useState<PytorchVersion[]>([]);
  const [loadingVersions, setLoadingVersions] = useState(true);
  const [versionError, setVersionError] = useState('');
  const [selectedPyTorch, setSelectedPyTorch] = useState('');
  const [selectedCuda, setSelectedCuda] = useState<CudaVariant | null>(null);
  const [bashScript, setBashScript] = useState('python train.py --epochs 100 --batch-size 32');
  const [zipFile, setZipFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const [requestPriority, setRequestPriority] = useState(false);
  const [priorityReason, setPriorityReason] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);
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

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!jobName || !selectedPyTorch || !selectedCuda) return;
    if (!zipFile) {
      setSubmitError('Please attach your workspace archive (.zip) before submitting.');
      return;
    }

    setSubmitting(true);
    setSubmitError('');
    try {
      const job = await submitJob({
        name: jobName,
        command: bashScript,
        pytorchVersion: selectedPyTorch,
        cudaVersion: selectedCuda.cuda,
        dockerBaseImage: selectedCuda.tag,
        requestForPriority: requestPriority,
        reasonForPriority: requestPriority ? priorityReason : undefined,
      }, zipFile);
      navigate(`/jobs/${job.id}`);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : 'Failed to submit job.');
    } finally {
      setSubmitting(false);
    }
  };

  const availableCudas = versions.find(v => v.version === selectedPyTorch)?.cudaVersions || [];

  return (
    <div className="fade-in" style={{ maxWidth: '800px', margin: '0 auto' }}>
      <h1>Submit New Job</h1>
      
      <div className="card">
        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label className="form-label">Job Name</label>
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
              <label className="form-label">PyTorch Version</label>
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
              <label className="form-label">CUDA / cuDNN Version</label>
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
            </div>
          </div>

          <div className="form-group">
            <label className="form-label">Workspace Archive (.zip)</label>
            <div className="upload-zone" onClick={handleFileClick}>
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
                  <p style={{ color: 'var(--text-primary)', fontWeight: 500 }}>Click to upload or drag and drop</p>
                  <p style={{ fontSize: '0.875rem' }}>ZIP file containing your training scripts and data</p>
                </div>
              )}
            </div>
            <div
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: '0.5rem',
                marginTop: '0.75rem',
                padding: '0.75rem',
                backgroundColor: 'rgba(245, 158, 11, 0.1)',
                border: '1px solid rgba(245, 158, 11, 0.35)',
                borderRadius: '6px',
                color: 'var(--status-warning, #f59e0b)',
                fontSize: '0.825rem',
                lineHeight: 1.5,
              }}
            >
              <AlertTriangle size={16} style={{ flexShrink: 0, marginTop: '0.15rem' }} />
              <span>
                Your ZIP must include a <strong>requirements.txt</strong>. Please pin <strong>absolute package
                versions</strong> compatible with the selected PyTorch {selectedPyTorch || ''} and CUDA{' '}
                {selectedCuda?.cuda || ''} to avoid version mismatch failures — Docker image building can take a
                significant amount of time.
              </span>
            </div>
          </div>

          <div className="form-group">
            <label className="form-label">Run Command (Bash)</label>
            <textarea 
              className="form-textarea"
              value={bashScript}
              onChange={e => setBashScript(e.target.value)}
              placeholder="python train.py"
              required
            ></textarea>
            <p style={{ fontSize: '0.75rem', marginTop: '0.5rem' }}>This command will be executed inside the container root of your extracted zip file.</p>
          </div>

          <div className="form-group" style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer', fontSize: '0.875rem' }}>
              <input 
                type="checkbox" 
                checked={requestPriority} 
                onChange={(e) => setRequestPriority(e.target.checked)} 
              />
              Request High Priority
            </label>
            
            {requestPriority && (
              <div style={{ marginTop: '0.5rem', animation: 'fadeIn 0.2s ease-out' }}>
                <label className="form-label">Reason for Priority</label>
                <textarea 
                  className="form-input"
                  style={{ minHeight: '60px' }}
                  value={priorityReason}
                  onChange={e => setPriorityReason(e.target.value)}
                  placeholder="Explain why this job should be prioritized..."
                ></textarea>
              </div>
            )}
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
              {submitting ? 'Submitting...' : 'Submit Job'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default SubmitJob;
