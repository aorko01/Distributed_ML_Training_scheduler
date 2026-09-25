import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { ArrowLeft, ArrowRight, Boxes, CheckCircle2, MonitorPlay, Search } from 'lucide-react';
import { interactive, type SourceJob } from '../services/interactive';

/**
 * Step 1 of interactive access: pick a built image (existing job) and give
 * the session a name. Machine requirements + eligible machines live on the
 * workspace detail page, not here.
 */
export default function InteractiveCreate() {
  const [searchParams] = useSearchParams();
  const requestedJobId = searchParams.get('job');
  const [name, setName] = useState('');
  const [jobs, setJobs] = useState<SourceJob[]>([]);
  const [job, setJob] = useState('');
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [reload, setReload] = useState(0);
  const submittingRef = useRef(false);
  const key = useRef<string | null>(null);
  const navigate = useNavigate();

  const selectedJob = jobs.find((j) => j.id === job) ?? null;
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return jobs;
    return jobs.filter((j) => `${j.name} ${j.id} ${j.source_image_label ?? ''}`.toLowerCase().includes(q));
  }, [jobs, query]);

  useEffect(() => { key.current = null; }, [name, job]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError('');
    (async () => {
      const choices = await interactive.sources();
      if (!active) return;
      setJobs(choices);
      if (requestedJobId && choices.some((c) => c.id === requestedJobId)) {
        setJob(requestedJobId);
      } else {
        setJob(choices[0]?.id ?? '');
      }
    })()
      .catch((err) => { if (active) setError(err instanceof Error ? err.message : 'Could not load builds'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [reload, requestedJobId]);

  async function create(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submittingRef.current || !name.trim() || !job) return;
    key.current ??= crypto.randomUUID();
    submittingRef.current = true;
    setSubmitting(true);
    setError('');
    try {
      const workspace = await interactive.create({ kind: 'job', name: name.trim(), sourceJobId: job }, key.current);
      navigate(`/interactive/${workspace.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Creation failed');
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  return (
    <div className="fade-in iw-create">
      <div className="builds-hero">
        <div>
          <span className="ws-eyebrow"><MonitorPlay size={14} /> Interactive session</span>
          <h1>New session from a build</h1>
          <p>Pick a built workspace image, name your session, then choose a machine on the next screen.</p>
        </div>
        <Link className="btn btn-secondary" to="/interactive"><ArrowLeft size={16} /> Sessions</Link>
      </div>

      <form onSubmit={create} className="card iw-create-card">
        <div className="form-group">
          <label className="form-label" htmlFor="session-name">Session name</label>
          <input
            id="session-name"
            className="form-input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={120}
            placeholder="e.g. debug-resnet50"
            required
          />
        </div>

        <div className="form-group">
          <label className="form-label" htmlFor="build-search">Source build</label>
          <div className="iw-search">
            <Search size={16} />
            <input
              id="build-search"
              className="form-input"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search builds by name or id…"
            />
          </div>
        </div>

        {loading && <p role="status" className="iw-muted">Loading builds…</p>}
        {error && (
          <div role="alert" className="training-error">
            <p style={{ margin: 0 }}>{error}</p>
            <button type="button" className="btn btn-secondary" style={{ marginTop: '0.6rem' }} onClick={() => setReload((v) => v + 1)}>Retry</button>
          </div>
        )}
        {!loading && !error && jobs.length === 0 && (
          <div className="builds-empty card">
            <Boxes size={28} color="var(--accent-primary)" />
            <h3>No built images yet</h3>
            <p>Build a workspace first, then come back to open it interactively.</p>
            <Link className="btn btn-primary" to="/submit">Add Workspace</Link>
          </div>
        )}

        {!loading && !error && jobs.length > 0 && (
          <div className="iw-pick-grid" role="radiogroup" aria-label="Source build">
            {visible.map((j) => {
              const active = j.id === job;
              return (
                <button
                  key={j.id}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  className={`iw-pick${active ? ' iw-pick--active' : ''}`}
                  onClick={() => setJob(j.id)}
                >
                  <span className="iw-pick-top">
                    <span className="iw-pick-name">{j.name}</span>
                    {active && <CheckCircle2 size={16} />}
                  </span>
                  <span className="iw-pick-id">{j.id}</span>
                  {j.source_image_label && <span className="iw-pick-base">{j.source_image_label}</span>}
                </button>
              );
            })}
            {visible.length === 0 && <p className="iw-muted">No builds match “{query}”.</p>}
          </div>
        )}

        {selectedJob?.source_image_label && (
          <p className="ws-hint">Image: <code>{selectedJob.source_image_label}</code></p>
        )}

        <div className="iw-create-foot">
          <span className="ws-hint">Next: pick requirements + machine, then open the editor or copy the VS Code command.</span>
          <button className="btn btn-primary" type="submit" disabled={submitting || loading || !name.trim() || !job}>
            {submitting ? 'Creating…' : <>Create session <ArrowRight size={16} /></>}
          </button>
        </div>
      </form>
    </div>
  );
}
