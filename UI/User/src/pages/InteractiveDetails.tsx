import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { interactive, type Workspace } from '../services/interactive';

export default function InteractiveDetails() {
  const { id } = useParams();
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [lines, setLines] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [cancelling, setCancelling] = useState(false);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    setWorkspace(null); setLines([]); setError('');
    async function load() {
      try {
        const [item, logs] = await Promise.all([interactive.detail(id!), interactive.logs(id!)]);
        if (!active) return;
        setWorkspace(item); setLines(logs.lines); setError('');
        if (['QUEUED', 'BUILDING'].includes(item.revision.state)) timer = setTimeout(load, 2000);
      } catch (err) { if (active) { setWorkspace(null); setLines([]); setError(err instanceof Error ? err.message : 'Could not load workspace'); } }
    }
    void load();
    return () => { active = false; clearTimeout(timer); };
  }, [id, reload]);
  async function cancel() {
    setCancelling(true);
    try { await interactive.cancel(id!); setReload(value => value + 1); }
    catch (err) { setError(err instanceof Error ? err.message : 'Cancellation failed'); }
    finally { setCancelling(false); }
  }
  return <div className="card">
    <Link to="/interactive">All workspaces</Link>
    {error && <div role="alert"><p>{error}</p><button className="btn btn-secondary" onClick={() => setReload(value => value + 1)}>Retry</button></div>}
    {!workspace && !error && <p role="status">Loading workspace…</p>}
    {workspace && <>
      <h1>{workspace.name}</h1>
      <p>Source: {workspace.source_type === 'UPLOAD' ? 'Uploaded workspace' : 'Existing job'} · Revision {workspace.revision.revision_number}</p>
      <p role="status">{workspace.revision.state === 'IMAGE_READY' ? 'Image ready' : workspace.revision.state}</p>
      {workspace.revision.failure_reason && <p role="alert">{workspace.revision.failure_reason}</p>}
      {workspace.revision.image_tag && <p>Image tag: <code>{workspace.revision.image_tag}</code></p>}
      {workspace.revision.image_digest_ref && <p title={workspace.revision.image_digest_ref}>Digest: <code>{workspace.revision.image_digest_ref.split('@')[1]?.slice(0, 23)}…</code></p>}
      <pre aria-label="Build logs" style={{ whiteSpace: 'pre-wrap' }}>{lines.join('\n') || 'No build logs yet.'}</pre>
      {['QUEUED', 'BUILDING'].includes(workspace.revision.state) && <button className="btn btn-secondary" disabled={cancelling} onClick={cancel}>Cancel build</button>}
      <p>Runtime placement is not yet available. This image is not running.</p>
      <button className="btn btn-secondary" disabled title="Runtime placement is not yet available">Connect</button>{' '}
      <button className="btn btn-secondary" disabled title="Runtime placement is not yet available">Save as new revision</button>
    </>}
  </div>;
}
