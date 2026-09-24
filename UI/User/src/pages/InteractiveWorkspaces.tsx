import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { interactive, type Workspace, type Runtime } from '../services/interactive';

export default function InteractiveWorkspaces() {
  const [items, setItems] = useState<Workspace[] | null>(null);
  const [runtimes, setRuntimes] = useState<Record<string, Runtime>>({});
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let active = true; setItems(null); setError('');
    (async () => {
      const values = await interactive.list();
      if (!active) return;
      setItems(values);
      const states: Record<string, Runtime> = {};
      await Promise.all(values.map(async (w) => {
        try {
          const r = await interactive.runtime(w.id);
          if (r && active) states[w.id] = r;
        } catch { /* a workspace without runtime is normal */ }
      }));
      if (active) setRuntimes(states);
    })().catch(err => { if (active) setError(err instanceof Error ? err.message : 'Could not load workspaces'); });
    return () => { active = false; };
  }, [reload]);
  return <div className="card"><h1>Interactive workspaces</h1>
    <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '1rem' }}>
      <Link className="btn btn-primary" to="/submit?mode=interactive">New interactive workspace</Link>
    </div>
    {error && <div role="alert"><p>{error}</p><button onClick={() => setReload(value => value + 1)}>Retry</button></div>}
    {!items && !error && <p>Loading workspaces…</p>}
    {items?.length === 0 && <p>No workspaces yet.</p>}
    <ul>{items?.map(item => {
      const runtime = runtimes[item.id];
      const live = runtime && !['STOPPED', 'FAILED'].includes(runtime.state);
      return <li key={item.id}>
        <Link to={`/interactive/${item.id}`}>{item.name}</Link> — {item.revision.state === 'IMAGE_READY' ? 'Image ready' : item.revision.state}
        {runtime && <span> · Runtime: {runtime.state.toLowerCase()}{live && ['QUEUED', 'ASSIGNED'].includes(runtime.state) ? ' (waiting for a matching machine)' : ''}</span>}
      </li>;
    })}</ul>
  </div>;
}
