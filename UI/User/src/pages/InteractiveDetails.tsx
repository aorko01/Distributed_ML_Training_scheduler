import { useEffect, useState, useRef } from 'react';
import { Link, useParams } from 'react-router-dom';
import { interactive, type Workspace, type Runtime } from '../services/interactive';

import { verifyConnection } from '../services/terminalVerification';

export default function InteractiveDetails() {
  const { id } = useParams();
  const runtimeLatest = useRef<Runtime | null>(null);
  const [runtime, setRuntime] = useState<Runtime | null>(null);
  const [busy, setBusy] = useState(false);
  const [connectionState, setConnectionState] = useState('');
  const connection = useRef<AbortController | null>(null);
  const connectionBusy = useRef(false);
  const currentRuntime = useRef('');
  const loadedRoute = useRef(id);
  const routeId = useRef(id); routeId.current = id;
  const startKey = useRef<string | null>(null);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [lines, setLines] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [cancelling, setCancelling] = useState(false);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    setWorkspace(null); setLines([]); setError('');

    connection.current?.abort(); setConnectionState('');
    if (loadedRoute.current !== id) {
      loadedRoute.current = id; setRuntime(null); runtimeLatest.current = null; currentRuntime.current = '';
    }
    async function load() {
      try {
        const [item, logs, running] = await Promise.all([interactive.detail(id!), interactive.logs(id!), interactive.runtime(id!)]);
        if (!active) return;
        const latest = runtimeLatest.current;
        if (latest && running && latest.generation > running.generation) { timer = setTimeout(load, 2000); return; }
        runtimeLatest.current = running;
        const identity = running ? `${running.id}:${running.generation}:${running.state === 'READY' && running.desired_state === 'RUNNING'}` : '';
        if (identity !== currentRuntime.current) { connection.current?.abort(); setConnectionState(''); currentRuntime.current = identity; }
        setRuntime(previous => previous && running && previous.generation > running.generation ? previous : running);
        setWorkspace(item); setLines(logs.lines); setError('');
        if (['QUEUED', 'BUILDING'].includes(item.revision.state) || (running && !['STOPPED','FAILED'].includes(running.state))) timer = setTimeout(load, 2000);
      } catch (err) { if (active) {
        connection.current?.abort(); currentRuntime.current = ''; setConnectionState(''); setRuntime(null);
        setWorkspace(null); setLines([]); setError(err instanceof Error ? err.message : 'Could not load workspace');
        timer = setTimeout(load, 2000);
      } }
    }
    void load();
    return () => { active = false; clearTimeout(timer); connection.current?.abort(); };
  }, [id, reload]);
  async function cancel() {
    setCancelling(true);
    try { await interactive.cancel(id!); setReload(value => value + 1); }
    catch (err) { setError(err instanceof Error ? err.message : 'Cancellation failed'); }
    finally { setCancelling(false); }
  }
  useEffect(() => { startKey.current = null; }, [id]);
  async function startRuntime() {
    if (busy) return;
    const requestedId = id;
    setBusy(true); setError('');
    startKey.current ??= crypto.randomUUID();
    try { const value = await interactive.start(id!, startKey.current); if (routeId.current !== requestedId) return; runtimeLatest.current = value; setRuntime(value); startKey.current = null; setReload(v => v + 1); }
    catch (err) { setError(err instanceof Error ? err.message : 'Start failed'); }
    finally { setBusy(false); }
  }
  async function stopRuntime() {
    if (!runtime || busy) return;
    const stoppingId = id;
    setBusy(true); connection.current?.abort(); setConnectionState('');
    try { const stopped = await interactive.stop(runtime.id); if (routeId.current !== stoppingId) return; runtimeLatest.current = stopped; setRuntime(stopped); setReload(v => v + 1); }
    catch (err) { setError(err instanceof Error ? err.message : 'Stop failed'); }
    finally { setBusy(false); }
  }
  async function connect() {
    if (!runtime || connectionBusy.current) return;
    connectionBusy.current = true;
    const controller = new AbortController(); connection.current = controller;
    const identity = currentRuntime.current;
    setConnectionState('Checking connection…');
    try {
      const grant = await interactive.connection(runtime.id, controller.signal);
      if (grant.runtime_id !== runtime.id || grant.generation !== runtime.generation) throw new Error('Runtime changed');
      await verifyConnection(grant, controller.signal);
      if (!controller.signal.aborted && identity === currentRuntime.current) setConnectionState('Connected successfully');
    } catch (err) {
      if (!controller.signal.aborted && identity === currentRuntime.current) setConnectionState(err instanceof Error ? err.message : 'Connection failed');
    } finally { connectionBusy.current = false; }
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
      <p>This runtime is temporary. Stop discards unsaved runtime changes; the saved source image remains available.</p>
      {runtime && <><p role="status">Runtime: {runtime.state.toLowerCase()}</p>
        {runtime.failure_detail && <p role="alert">{runtime.failure_detail}</p>}
        {runtime.lifetime_deadline && <p>Runtime deadline: {runtime.lifetime_deadline}</p>}</>}
      {workspace.revision.state === 'IMAGE_READY' && (!runtime || ['STOPPED','FAILED'].includes(runtime.state)) &&
        <button className="btn" disabled={busy} onClick={startRuntime}>Start</button>}{' '}
      {runtime && !['STOPPED','FAILED'].includes(runtime.state) && <button className="btn btn-secondary" disabled={busy || runtime.desired_state === 'STOPPED'} onClick={stopRuntime}>Stop</button>}{' '}
      <button className="btn btn-secondary" disabled={busy || runtime?.state !== 'READY' || runtime.desired_state !== 'RUNNING' || connectionState === 'Checking connection…'} onClick={connect}>Connect</button>{' '}
      <button className="btn btn-secondary" disabled title="Saving is not available in this phase">Save as new revision</button>
      {connectionState && <p role="status">{connectionState}</p>}
    </>}
  </div>;
}
