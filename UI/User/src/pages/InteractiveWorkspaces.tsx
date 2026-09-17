import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { interactive, type Workspace } from '../services/interactive';
export default function InteractiveWorkspaces() {
  const [items, setItems] = useState<Workspace[] | null>(null);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let active = true; setItems(null); setError('');
    interactive.list().then(values => { if (active) setItems(values); })
      .catch(err => { if (active) setError(err instanceof Error ? err.message : 'Could not load workspaces'); });
    return () => { active = false; };
  }, [reload]);
  return <div className="card"><h1>Interactive workspaces</h1><Link to="/submit?mode=interactive">Create workspace</Link>
    {error && <div role="alert"><p>{error}</p><button onClick={() => setReload(value => value + 1)}>Retry</button></div>}
    {!items && !error && <p>Loading workspaces…</p>}
    {items?.length === 0 && <p>No workspaces yet.</p>}
    <ul>{items?.map(item => <li key={item.id}><Link to={`/interactive/${item.id}`}>{item.name}</Link> — {item.revision.state === 'IMAGE_READY' ? 'Image ready' : item.revision.state}</li>)}</ul>
  </div>;
}
