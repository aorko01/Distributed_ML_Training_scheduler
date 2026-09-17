import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { interactive, type Choice, type SourceJob, type Creation } from '../services/interactive';

export default function InteractiveCreate() {
  const [source, setSource] = useState<'upload' | 'job'>('upload');
  const [name, setName] = useState('');
  const [bases, setBases] = useState<Choice[]>([]);
  const [jobs, setJobs] = useState<SourceJob[]>([]);
  const [base, setBase] = useState('');
  const [job, setJob] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [reload, setReload] = useState(0);
  const submittingRef = useRef(false);
  const key = useRef<string | null>(null);
  const navigate = useNavigate();
  useEffect(() => { key.current = null; }, [source, name, base, job, file]);
  useEffect(() => {
    let active = true;
    setLoading(true); setError(''); setBases([]); setJobs([]);
    const load = source === 'upload' ? interactive.bases() : interactive.sources();
    load.then(values => {
      if (!active) return;
      if (source === 'upload') {
        const choices = values as Choice[]; setBases(choices); setBase(choices[0]?.id ?? '');
      } else {
        const choices = values as SourceJob[]; setJobs(choices); setJob(choices[0]?.id ?? '');
      }
    }).catch(err => { if (active) setError(err instanceof Error ? err.message : 'Could not load choices'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [source, reload]);
  async function submit(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submittingRef.current) return;
    if (source === 'upload' && !file) { setError('Attach a ZIP containing requirements.txt.'); return; }
    const input: Creation = source === 'upload'
      ? { kind: 'upload', name, baseImageId: base, file: file! }
      : { kind: 'job', name, sourceJobId: job };
    key.current ??= crypto.randomUUID();
    submittingRef.current = true; setSubmitting(true); setError('');
    try { const workspace = await interactive.create(input, key.current); navigate(`/interactive/${workspace.id}`); }
    catch (err) { setError(err instanceof Error ? err.message : 'Creation failed'); }
    finally { submittingRef.current = false; setSubmitting(false); }
  }
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
          <div className="form-group"><label className="form-label" htmlFor="base">PyTorch/CUDA base image</label>
            <select id="base" className="form-select" value={base} onChange={e => setBase(e.target.value)} disabled={loading} required>
              {bases.map(value => <option key={value.id} value={value.id}>{value.label}</option>)}
            </select></div>
          <div className="form-group"><label className="form-label" htmlFor="archive">Workspace ZIP (requirements.txt required, up to 64 MiB)</label>
            <input id="archive" type="file" accept=".zip" required onChange={e => setFile(e.target.files?.[0] ?? null)} /></div>
        </> : <div className="form-group"><label className="form-label" htmlFor="source-job">Existing job</label>
          <select id="source-job" className="form-select" value={job} onChange={e => setJob(e.target.value)} disabled={loading} required>
            {jobs.map(value => <option key={value.id} value={value.id}>{value.name}</option>)}
          </select>{!loading && jobs.length === 0 && !error && <p>No jobs with an available image.</p>}</div>}
        {loading && <p role="status">Loading choices…</p>}
        {error && <div role="alert"><p>{error}</p><button type="button" className="btn btn-secondary" onClick={() => setReload(value => value + 1)}>Reload choices</button></div>}
        <button className="btn btn-primary" type="submit" disabled={submitting || loading || !name.trim() || (source === 'upload' ? !base || !file : !job)}>
          {submitting ? 'Creating…' : 'Create workspace'}
        </button>
      </fieldset>
    </form>
  </div>;
}
