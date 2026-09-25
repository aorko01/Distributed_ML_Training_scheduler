import { useEffect, useState, useRef } from 'react';
import { Link, useParams } from 'react-router-dom';
import { interactive, interactiveCapacity, type Workspace, type Runtime, type CapacityOptions, type ResourceRequirements } from '../services/interactive';
import { ResourceRequirementsForm } from '../features/interactive-capacity/ResourceRequirementsForm';
import { CapacitySummary } from '../features/interactive-capacity/CapacitySummary';
import { MachineGrid } from '../features/interactive-capacity/MachineGrid';
import { useCapacityPreview } from '../features/interactive-capacity/useCapacityPreview';
import { normalizeRequirements, requirementsValid } from '../features/interactive-capacity/requirements';

import { verifyConnection } from '../services/terminalVerification';

function runtimeLabel(state: string): string {
  if (state === 'QUEUED') return 'Waiting for a matching machine';
  if (['ASSIGNED', 'PULLING', 'STARTING', 'CONNECTING'].includes(state)) return `Preparing (${state.toLowerCase()})`;
  return state.toLowerCase();
}

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
  const [capOptions, setCapOptions] = useState<CapacityOptions | null>(null);
  const [requirements, setRequirements] = useState<ResourceRequirements | null>(null);
  const liveRuntime = runtime && !['STOPPED', 'FAILED'].includes(runtime.state);
  const { preview, loading: previewLoading, error: previewError } = useCapacityPreview(liveRuntime ? null : requirements, !liveRuntime);
  const valid = requirements ? requirementsValid(requirements, capOptions) : false;
  useEffect(() => {
    interactiveCapacity.options().then(setCapOptions).catch(() => {});
  }, []);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    setWorkspace(null); setLines([]); setError('');

    connection.current?.abort(); setConnectionState('');
    if (loadedRoute.current !== id) {
      loadedRoute.current = id; setRuntime(null); runtimeLatest.current = null; currentRuntime.current = ''; setRequirements(null);
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
        setRequirements((prev) => {
          if (prev) return prev;
          const fromRuntime = running?.requirements ?? null;
          const fromWorkspace = item.default_resource_requirements ?? null;
          const fallback = capOptions?.defaults ?? { gpu_model: null, minimum_vram_gb: 4, cpu_cores: 2, memory_gb: 8, disk_gb: 20 };
          return normalizeRequirements((fromRuntime ?? fromWorkspace ?? fallback) as ResourceRequirements, capOptions?.defaults ?? null);
        });
        if (['QUEUED', 'BUILDING'].includes(item.revision.state) || (running && !['STOPPED','FAILED'].includes(running.state))) timer = setTimeout(load, 2000);
      } catch (err) { if (active) {
        connection.current?.abort(); currentRuntime.current = ''; setConnectionState(''); setRuntime(null);
        setWorkspace(null); setLines([]); setError(err instanceof Error ? err.message : 'Could not load workspace');
        timer = setTimeout(load, 2000);
      } }
    }
    void load();
    return () => { active = false; clearTimeout(timer); connection.current?.abort(); };
  }, [id, reload, capOptions === null]);
  async function cancel() {
    setCancelling(true);
    try { await interactive.cancel(id!); setReload(value => value + 1); }
    catch (err) { setError(err instanceof Error ? err.message : 'Cancellation failed'); }
    finally { setCancelling(false); }
  }
  useEffect(() => { startKey.current = null; }, [id]);
  async function startRuntime() {
    if (busy || !requirements) return;
    const requestedId = id;
    setBusy(true); setError('');
    startKey.current ??= crypto.randomUUID();
    try { const value = await interactive.start(id!, startKey.current, requirements); if (routeId.current !== requestedId) return; runtimeLatest.current = value; setRuntime(value); startKey.current = null; setReload(v => v + 1); }
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
  const imageReady = workspace?.revision.state === 'IMAGE_READY';
  const canRequest = imageReady && (!runtime || ['STOPPED', 'FAILED'].includes(runtime.state)) && valid && !!requirements;
  const noCapable = preview && preview.matching_online === 0;
  const allBusy = preview && preview.matching_online > 0 && preview.available_now === 0;
  return <div className="card">
    <Link to="/interactive">All workspaces</Link>
    {error && <div role="alert"><p>{error}</p><button className="btn btn-secondary" onClick={() => setReload(value => value + 1)}>Retry</button></div>}
    {!workspace && !error && <p role="status">Loading workspace…</p>}
    {workspace && <>
      <h1>{workspace.name}</h1>
      <p>Source: {workspace.source_type === 'UPLOAD' ? 'Uploaded workspace' : 'Existing job'} · Revision {workspace.revision.revision_number}</p>
      {workspace.source_type === 'UPLOAD' && workspace.revision.requested_base_image && <p>Base image: <code>{workspace.revision.requested_base_image}</code></p>}
      {workspace.source_type !== 'UPLOAD' && workspace.revision.source_image_tag && <p>Base image inherited from existing job: <code>{workspace.revision.source_image_tag}</code></p>}
      <p role="status">{workspace.revision.state === 'IMAGE_READY' ? 'Image ready' : workspace.revision.state}</p>
      {workspace.revision.failure_reason && <p role="alert">{workspace.revision.failure_reason}</p>}
      {workspace.revision.image_tag && <p>Image tag: <code>{workspace.revision.image_tag}</code></p>}
      {workspace.revision.image_digest_ref && <p title={workspace.revision.image_digest_ref}>Digest: <code>{workspace.revision.image_digest_ref.split('@')[1]?.slice(0, 23)}…</code></p>}
      <pre aria-label="Build logs" style={{ whiteSpace: 'pre-wrap' }}>{lines.join('\n') || 'No build logs yet.'}</pre>
      {['QUEUED', 'BUILDING'].includes(workspace.revision.state) && <button className="btn btn-secondary" disabled={cancelling} onClick={cancel}>Cancel build</button>}
      <p>This runtime is temporary. Stop discards unsaved runtime changes; the saved source image remains available.</p>
      {runtime && <><p role="status">Runtime: {runtimeLabel(runtime.state)}</p>
        {runtime.failure_detail && <p role="alert">{runtime.failure_detail}</p>}
        {runtime.assigned_machine && <p>Assigned machine: {runtime.assigned_machine.display_name} · {runtime.assigned_machine.gpu_model ?? 'GPU'} · {runtime.assigned_machine.total_vram_gb?.toFixed(0) ?? '?'} GB VRAM</p>}
        {runtime.lifetime_deadline && <p>Runtime deadline: {runtime.lifetime_deadline}</p>}</>}
      {imageReady && (!runtime || ['STOPPED','FAILED'].includes(runtime.state)) && <>
        <h2>Request interactive access</h2>
        {!imageReady && <p role="status">Workspace image is still building.</p>}
        {requirements && <ResourceRequirementsForm value={requirements} options={capOptions} onChange={setRequirements} />}
        {!valid && <p role="alert" className="error-text">Requirements are outside operator bounds.</p>}
        <div style={{ marginTop: '1rem' }}>
          <CapacitySummary preview={preview} loading={previewLoading} />
          {previewError && <p role="alert" className="error-text">{previewError}</p>}
        </div>
        <div style={{ marginTop: '1rem' }}>
          <MachineGrid machines={preview?.machines ?? []} />
        </div>
        {noCapable && <p role="status">No capable online machine exists. Reduce requirements or wait for a suitable worker to come online.</p>}
        {allBusy && <p role="status">Matching machines are busy. You can queue and the scheduler will assign one when free.</p>}
        <button className="btn" disabled={busy || !canRequest || !!noCapable} onClick={startRuntime}>
          {allBusy ? 'Queue interactive access' : 'Request interactive access'}
        </button>
      </>}
      {runtime && (runtime as unknown as { ssh_ready?: boolean; ssh_status?: string; ssh_generation?: number }).ssh_ready && runtime.state === 'READY' && <>
        <h2>Connect with VS Code</h2>
        <p>Native Remote-SSH into the workload container (<code>dml</code>, <code>/workspace</code>). Live only: Stop ends SSH immediately.</p>
        <pre>{`dml-ssh configure ${runtime.id}\n# Remote-SSH: Connect to Host -> dml-${runtime.id}-g${(runtime as unknown as { ssh_generation?: number }).ssh_generation ?? runtime.generation}\n# Open folder /workspace`}</pre>
      </>}
      {runtime && !(runtime as unknown as { ssh_ready?: boolean }).ssh_ready && runtime.state === 'READY' && (runtime as unknown as { ssh_capable?: boolean }).ssh_capable !== true && <p role="status">SSH unavailable for this runtime (rebuild from an SSH-capable revision).</p>}
      {runtime && !['STOPPED','FAILED'].includes(runtime.state) && <button className="btn btn-secondary" disabled={busy || runtime.desired_state === 'STOPPED'} onClick={stopRuntime}>Stop</button>}{' '}
      <button className="btn btn-secondary" disabled={busy || runtime?.state !== 'READY' || runtime.desired_state !== 'RUNNING' || connectionState === 'Checking connection…'} onClick={connect}>Connect</button>{' '}
      {runtime?.state === 'READY' && <Link className="btn" to={`/interactive/${id}/editor`}>Open Editor</Link>}{' '}
      {runtime && !runtime.editor_capable && <span title="Start a new editor-capable runtime after the workspace editor rollout is enabled">Editor unavailable for this runtime</span>}{' '}
      <button className="btn btn-secondary" disabled title="Saving is not available in this phase">Save as new revision</button>
      {connectionState && <p role="status">{connectionState}</p>}
    </>}
  </div>;
}
