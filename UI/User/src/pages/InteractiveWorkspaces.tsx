import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ArrowRight, Loader2, MonitorPlay, Plus, Search, SquarePen } from 'lucide-react';
import { interactive, type Workspace, type Runtime } from '../services/interactive';

type Filter = 'all' | 'ready' | 'running' | 'building';

function imageBadge(state: string): string {
  switch (state) {
    case 'IMAGE_READY': return 'badge badge-ready';
    case 'BUILDING': return 'badge badge-building';
    case 'QUEUED': return 'badge badge-pending';
    case 'FAILED': return 'badge badge-failed';
    case 'CANCELLED': return 'badge badge-offline';
    default: return 'badge badge-pending';
  }
}

function imageLabel(state: string): string {
  switch (state) {
    case 'IMAGE_READY': return 'Image ready';
    case 'BUILDING': return 'Building';
    case 'QUEUED': return 'Queued';
    case 'FAILED': return 'Failed';
    case 'CANCELLED': return 'Cancelled';
    default: return state;
  }
}

function runtimeBadge(state: string): string {
  if (state === 'READY') return 'badge badge-success';
  if (['QUEUED', 'ASSIGNED'].includes(state)) return 'badge badge-pending';
  if (['PULLING', 'STARTING', 'CONNECTING'].includes(state)) return 'badge badge-running';
  if (['STOPPED', 'FAILED'].includes(state)) return 'badge badge-offline';
  return 'badge badge-running';
}

export default function InteractiveWorkspaces() {
  const [items, setItems] = useState<Workspace[] | null>(null);
  const [runtimes, setRuntimes] = useState<Record<string, Runtime>>({});
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const navigate = useNavigate();

  useEffect(() => {
    let active = true;
    setItems(null);
    setError('');
    (async () => {
      const values = await interactive.list();
      if (!active) return;
      setItems(values);
      const states: Record<string, Runtime> = {};
      await Promise.all(values.map(async (w) => {
        try {
          const r = await interactive.runtime(w.id);
          if (r && active) states[w.id] = r;
        } catch { /* no runtime yet */ }
      }));
      if (active) setRuntimes(states);
    })().catch((err) => { if (active) setError(err instanceof Error ? err.message : 'Could not load sessions'); });
    return () => { active = false; };
  }, [reload]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (items ?? []).filter((w) => {
      const r = runtimes[w.id];
      const live = !!r && !['STOPPED', 'FAILED'].includes(r.state);
      if (filter === 'ready' && w.revision.state !== 'IMAGE_READY') return false;
      if (filter === 'running' && !live) return false;
      if (filter === 'building' && !['QUEUED', 'BUILDING'].includes(w.revision.state)) return false;
      if (q && !`${w.name} ${w.id}`.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [items, runtimes, query, filter]);

  return (
    <div className="fade-in builds-page">
      <div className="builds-hero">
        <div>
          <span className="ws-eyebrow"><MonitorPlay size={14} /> Interactive sessions</span>
          <h1>Sessions</h1>
          <p>Built images you can open on a GPU machine — in the browser or via VS Code.</p>
        </div>
        <button className="btn btn-primary" onClick={() => navigate('/interactive/new')}>
          <Plus size={16} /> New session
        </button>
      </div>

      <div className="card builds-toolbar">
        <div className="iw-search builds-search">
          <Search size={16} />
          <input
            className="form-input"
            placeholder="Search sessions by name or id…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <div className="builds-filters">
          <label>Show
            <select className="form-select" value={filter} onChange={(e) => setFilter(e.target.value as Filter)}>
              <option value="all">All sessions</option>
              <option value="ready">Image ready</option>
              <option value="running">Running</option>
              <option value="building">Building</option>
            </select>
          </label>
        </div>
      </div>

      {error && (
        <div className="card training-error" role="alert">
          {error}
          <div style={{ marginTop: '0.6rem' }}>
            <button className="btn btn-secondary" onClick={() => setReload((v) => v + 1)}>Retry</button>
          </div>
        </div>
      )}
      {!items && !error && (
        <div style={{ display: 'flex', justifyContent: 'center', padding: '3rem' }}>
          <Loader2 className="animate-spin" size={30} />
        </div>
      )}
      {items && visible.length === 0 && (
        <div className="card builds-empty">
          <MonitorPlay size={30} color="var(--accent-primary)" />
          <h3>{items.length === 0 ? 'No sessions yet' : 'No sessions match'}</h3>
          <p>{items.length === 0 ? 'Create one from a built workspace image.' : 'Try a different search or filter.'}</p>
          {items.length === 0 && <button className="btn btn-primary" onClick={() => navigate('/interactive/new')}>New session</button>}
        </div>
      )}

      <div className="builds-grid">
        {visible.map((item) => {
          const runtime = runtimes[item.id];
          const ready = runtime?.state === 'READY';
          return (
            <div
              key={item.id}
              className="card build-card"
              role="button"
              tabIndex={0}
              onClick={() => navigate(`/interactive/${item.id}`)}
              onKeyDown={(e) => {
                if (e.target !== e.currentTarget) return;
                if (e.key === 'Enter' || e.key === ' ') navigate(`/interactive/${item.id}`);
              }}
            >
              <div className="build-card-top">
                <span className="build-card-name">{item.name}</span>
                <span className={imageBadge(item.revision.state)}>{imageLabel(item.revision.state)}</span>
              </div>
              <div className="build-card-meta build-card-id">{item.id}</div>
              <div className="iw-card-badges">
                {runtime ? (
                  <span className={runtimeBadge(runtime.state)}>
                    {runtime.state === 'READY' ? 'Live' : runtime.state.toLowerCase()}
                  </span>
                ) : (
                  <span className="badge badge-offline">No runtime</span>
                )}
                {runtime?.assigned_machine && <span className="iw-machine-tag">{runtime.assigned_machine.display_name}</span>}
              </div>
              <div className="build-card-foot">
                <span>Rev {item.revision.revision_number}</span>
                <span className="build-card-link" style={{ display: 'inline-flex', gap: '0.35rem' }}>
                  {ready ? (
                    <>
                      <Link
                        to={`/interactive/${item.id}/editor`}
                        onClick={(e) => e.stopPropagation()}
                        title="Open browser editor"
                        style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}
                      >
                        <SquarePen size={14} /> Editor
                      </Link>
                      <span aria-hidden="true">·</span>
                    </>
                  ) : null}
                  Open <ArrowRight size={14} />
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
