import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { interactive, type SourceJob, type Creation } from '../services/interactive';
import { fetchPytorchVersions, type PytorchVersion, type CudaVariant } from '../services/docker';

export default function InteractiveCreate() {
  const [source, setSource] = useState<'upload' | 'job'>('upload');
  const [name, setName] = useState('');
  const [versions, setVersions] = useState<PytorchVersion[]>([]);
  const [jobs, setJobs] = useState<SourceJob[]>([]);
  const [selectedPyTorch, setSelectedPyTorch] = useState('');
  const [selectedCuda, setSelectedCuda] = useState<CudaVariant | null>(null);
  const [job, setJob] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [confirmEmpty, setConfirmEmpty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [reload, setReload] = useState(0);
  const submittingRef = useRef(false);
  const key = useRef<string | null>(null);
  const navigate = useNavigate();
  const base = selectedCuda?.tag ?? '';
  useEffect(() => { key.current = null; }, [source, name, base, job, file]);
  useEffect(() => {
    let active = true;
    setLoading(true); setError(''); setVersions([]); setJobs([]);
    const load = async () => {
      if (source === 'upload') {
        const data = await fetchPytorchVersions();
        if (!active) return;
        setVersions(data);
        if (data.length > 0) {
          setSelectedPyTorch(data[0].version);
          setSelectedCuda(data[0].cudaVersions[0] ?? null);
        }
      } else {
        const choices = await interactive.sources();
        if (!active) return;
        setJobs(choices); setJob(choices[0]?.id ?? '');
      }
    };
    load().catch(err => { if (active) setError(err instanceof Error ? err.message : 'Could not load choices'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [source, reload]);

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

  const availableCudas = versions.find(v => v.version === selectedPyTorch)?.cudaVersions || [];

  async function submit(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submittingRef.current) return;
    if (source === 'upload' && !file) { setConfirmEmpty(true); return; }
    await create();
  }

  async function create() {
    setConfirmEmpty(false);
    if (submittingRef.current) return;
    const input: Creation = source === 'upload'
      ? { kind: 'upload', name, baseImageId: base, file }
      : { kind: 'job', name, sourceJobId: job };
    key.current ??= crypto.randomUUID();
    submittingRef.current = true; setSubmitting(true); setError('');
    try { const workspace = await interactive.create(input, key.current); navigate(`/interactive/${workspace.id}`); }
    catch (err) { setError(err instanceof Error ? err.message : 'Creation failed'); }
    finally { submittingRef.current = false; setSubmitting(false); }
  }

  if (confirmEmpty) return <div className="card">
    <div role="alertdialog" aria-modal="true" aria-labelledby="empty-workspace-title" style={{ maxWidth: 480, margin: '0 auto', textAlign: 'center' }}>
      <h2 id="empty-workspace-title" style={{ marginTop: 0 }}>Create an empty workspace?</h2>
      <p>No ZIP archive was submitted, so this workspace will start completely fresh — it contains no files or pre-installed packages beyond the selected PyTorch base image.</p>
      <p>You can add your code and dependencies later from the workspace editor.</p>
      <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'center', marginTop: '1.5rem' }}>
        <button type="button" className="btn btn-secondary" onClick={() => setConfirmEmpty(false)}>Cancel</button>
        <button type="button" className="btn btn-primary" onClick={create} disabled={submitting}>{submitting ? 'Creating…' : 'Create empty workspace'}</button>
      </div>
    </div>
  </div>;
  return <div className="card">
    <p>Create an isolated interactive workspace image. Runtime placement is not yet available.</p>
    <form onSubmit={submit}>
      <fieldset disabled={submitting} style={{ border: 0, padding: 0 }}>
        <div className="form-group"><label className="form-label" htmlFor="source">Workspace source</label>
          <select id="source" className="form-select" value={source} onChange={e => setSource(e.target.value as 'upload' | 'job')}>
            <option value="upload">Upload a new workspace</option><option value="job">Use an existing job</option>
          </select></div>
        <div className="form-group"><label className="form-label" htmlFor="workspace-name">Workspace name</label>
          <input id="workspace-name" className="form-input" value={name} onChange={e => setName(e.target.value)} maxLength={120} required /></div>
        {source === 'upload' ? <>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.5rem' }}>
            <div className="form-group"><label className="form-label" htmlFor="pytorch-version">PyTorch Version</label>
              <select id="pytorch-version" className="form-select" value={selectedPyTorch} onChange={handlePyTorchChange} disabled={loading || versions.length === 0} required>
                {loading && <option>Loading versions...</option>}
                {!loading && versions.length === 0 && !error && <option>No versions available</option>}
                {versions.map(v => <option key={v.version} value={v.version}>{v.version}</option>)}
              </select></div>
            <div className="form-group"><label className="form-label" htmlFor="cuda-version">CUDA / cuDNN Version</label>
              <select id="cuda-version" className="form-select" value={selectedCuda?.tag ?? ''} onChange={e => {
                const variant = availableCudas.find(v => v.tag === e.target.value);
                setSelectedCuda(variant ?? null);
              }} disabled={loading || availableCudas.length === 0} required>
                {loading && <option>Loading CUDA versions...</option>}
                {!loading && availableCudas.length === 0 && !error && <option>No CUDA variants</option>}
                {availableCudas.map(c => <option key={c.tag} value={c.tag}>CUDA {c.cuda} / cuDNN {c.cudnn}</option>)}
              </select></div>
          </div>
          <div className="form-group"><label className="form-label" htmlFor="archive">Workspace ZIP (optional — leave empty to start fresh, up to 64 MiB)</label>
            <input id="archive" type="file" accept=".zip" onChange={e => setFile(e.target.files?.[0] ?? null)} /></div>
        </> : <div className="form-group"><label className="form-label" htmlFor="source-job">Existing job</label>
          <select id="source-job" className="form-select" value={job} onChange={e => setJob(e.target.value)} disabled={loading} required>
            {jobs.map(value => <option key={value.id} value={value.id}>{value.name}</option>)}
          </select>{!loading && jobs.length === 0 && !error && <p>No jobs with an available image.</p>}</div>}
        {loading && <p role="status">Loading choices…</p>}
        {error && <div role="alert"><p>{error}</p><button type="button" className="btn btn-secondary" onClick={() => setReload(value => value + 1)}>Reload choices</button></div>}
        <button className="btn btn-primary" type="submit" disabled={submitting || loading || !name.trim() || (source === 'upload' ? !base : !job)}>
          {submitting ? 'Creating…' : 'Create workspace'}
        </button>
      </fieldset>
    </form>
  </div>;
}
