import { useEffect, useState, useRef } from 'react';
import { Link, useParams } from 'react-router-dom';
import { interactive, interactiveCapacity, type Workspace, type Runtime, type CapacityOptions, type ResourceRequirements, type RevisionHistory, type TrainingSettings } from '../services/interactive';
import { TrainingSettingsForm } from '../features/workspace/components/TrainingSettingsForm';
import { schedulerOrigin } from '../services/api';
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
  const [history, setHistory] = useState<RevisionHistory | null>(null);
  const [selectedRevision, setSelectedRevision] = useState<string | null>(null);
  const [trainRevision, setTrainRevision] = useState<string | null>(null);
  const [trainSettings, setTrainSettings] = useState<TrainingSettings>({ name: 'Workspace training', command: 'python train.py', priority: 'NORMAL' });
  const [trainingJobId, setTrainingJobId] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<string | null>(null);
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
        const [item, logs, running, revisions] = await Promise.all([interactive.detail(id!), interactive.logs(id!), interactive.runtime(id!), interactive.revisions(id!)]);
        if (!active) return;
        const latest = runtimeLatest.current;
        if (latest && running && latest.generation > running.generation) { timer = setTimeout(load, 2000); return; }
        runtimeLatest.current = running;
        const identity = running ? `${running.id}:${running.generation}:${running.state === 'READY' && running.desired_state === 'RUNNING'}` : '';
        if (identity !== currentRuntime.current) { connection.current?.abort(); setConnectionState(''); currentRuntime.current = identity; }
        setRuntime(previous => previous && running && previous.generation > running.generation ? previous : running);
        setWorkspace(item); setLines(logs.lines); setError('');
        setHistory(revisions);
        setRequirements((prev) => {
          if (prev) return prev;
          const fromRuntime = running?.requirements ?? null;
          const fromWorkspace = item.default_resource_requirements ?? null;
          const fallback = capOptions?.defaults ?? { gpu_model: null, minimum_vram_gb: 4, cpu_cores: 2, memory_gb: 8, disk_gb: 20 };
          return normalizeRequirements((fromRuntime ?? fromWorkspace ?? fallback) as ResourceRequirements, capOptions?.defaults ?? null);
        });
        if (['QUEUED', 'BUILDING'].includes(item.revision.state) || (running && !['STOPPED','FAILED'].includes(running.state)) || saveState) timer = setTimeout(load, 2000);
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
  async function pollDetailSave(operationId: string) {
    if (routeId.current !== id) return;
    try {
      const status = await interactive.saveStatus(operationId);
      if (routeId.current !== id) return;
      setSaveState(status.state);
      if (['REQUESTED', 'CAPTURING', 'UPLOADING', 'PUBLISH_QUEUED', 'PUBLISHING'].includes(status.state)) {
        window.setTimeout(() => void pollDetailSave(operationId), 3000);
      } else {
        sessionStorage.removeItem(`dml-detail-save:${id}`);
        sessionStorage.removeItem(`dml-detail-save-key:${status.runtime_id}:${status.generation}`);
        setBusy(false);
        setReload(v => v + 1);
        if (status.state === 'FAILED') setError(status.failure_code ?? 'Save failed; runtime remains available');
      }
    } catch { window.setTimeout(() => void pollDetailSave(operationId), 5000); }
  }
  async function pollDetailTraining(submissionId: string) {
    if (routeId.current !== id) return;
    try {
      const current = await interactive.trainingStatus(submissionId);
      if (routeId.current !== id) return;
      if (current.state === 'JOB_CREATED' || current.state === 'FAILED') {
        sessionStorage.removeItem(`dml-detail-training:${id}`);
        if (current.revision_id) sessionStorage.removeItem(`dml-detail-training-key:${id}:${current.revision_id}`);
        setBusy(false);
        if (current.state === 'JOB_CREATED') { setTrainingJobId(current.job_id); setTrainRevision(null); }
        else setError(current.failure_code ?? 'Training submission failed');
      } else window.setTimeout(() => void pollDetailTraining(submissionId), 3000);
    } catch { window.setTimeout(() => void pollDetailTraining(submissionId), 5000); }
  }
  useEffect(() => {
    if (!id) return;
    const saveId = sessionStorage.getItem(`dml-detail-save:${id}`);
    const trainingId = sessionStorage.getItem(`dml-detail-training:${id}`);
    if (saveId) { setBusy(true); void pollDetailSave(saveId); }
    if (trainingId) { setBusy(true); void pollDetailTraining(trainingId); }
  }, [id]);
  async function startRuntime() {
    if (busy || !requirements) return;
    const requestedId = id;
    setBusy(true); setError('');
    startKey.current ??= crypto.randomUUID();
    try { const value = await interactive.start(id!, startKey.current, requirements, selectedRevision ?? undefined); if (routeId.current !== requestedId) return; runtimeLatest.current = value; setRuntime(value); startKey.current = null; setReload(v => v + 1); }
    catch (err) { setError(err instanceof Error ? err.message : 'Start failed'); }
    finally { setBusy(false); }
  }
  async function stopRuntime() {
    if (!runtime || busy) return;
    const stoppingId = id;
    setBusy(true); setError('');
    try {
      if (runtime.save_enabled && runtime.state === 'READY') {
        const keyName = `dml-detail-save-key:${runtime.id}:${runtime.generation}`;
        const key = sessionStorage.getItem(keyName) ?? crypto.randomUUID().replace(/-/g, '');
        sessionStorage.setItem(keyName, key);
        const save = await interactive.saveAndStop(runtime.id, key, runtime.generation, runtime.revision_id);
        setSaveState(save.state);
        sessionStorage.setItem(`dml-detail-save:${id}`, save.id);
        void pollDetailSave(save.id);
      } else {
        const stopped = await interactive.stop(runtime.id);
        if (routeId.current !== stoppingId) return;
        connection.current?.abort(); setConnectionState('');
        runtimeLatest.current = stopped; setRuntime(stopped); setReload(v => v + 1);
      }
    }
    catch (err) { setError(err instanceof Error ? err.message : 'Stop failed'); }
    finally { if (!runtime.save_enabled || runtime.state !== 'READY') setBusy(false); }
  }
  async function discardRuntime() {
    if (!runtime || busy || !window.confirm('Discard changes made since the last saved revision and stop this runtime?')) return;
    setBusy(true); setError('');
    try {
      const stopped = await interactive.stop(runtime.id);
      connection.current?.abort(); setConnectionState('');
      runtimeLatest.current = stopped; setRuntime(stopped); setReload(v => v + 1);
    } catch (err) { setError(err instanceof Error ? err.message : 'Stop failed'); }
    finally { setBusy(false); }
  }
  async function submitSavedRevision() {
    if (!id || !trainRevision) return;
    setBusy(true); setError('');
    try {
      const keyName = `dml-detail-training-key:${id}:${trainRevision}`;
      const key = sessionStorage.getItem(keyName) ?? crypto.randomUUID().replace(/-/g, '');
      sessionStorage.setItem(keyName, key);
      const sub = await interactive.trainRevision(id, trainRevision, key, trainSettings);
      sessionStorage.setItem(`dml-detail-training:${id}`, sub.id);
      void pollDetailTraining(sub.id);
    } catch (err) { setError(err instanceof Error ? err.message : 'Training submission failed'); setBusy(false); }
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
  const imageReady = workspace?.saved_revision?.state === 'IMAGE_READY' || workspace?.revision.state === 'IMAGE_READY';
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
      {workspace.revision.ssh_hint && <p role="status">VS Code Remote-SSH: {workspace.revision.ssh_hint}.</p>}
      <pre aria-label="Build logs" style={{ whiteSpace: 'pre-wrap' }}>{lines.join('\n') || 'No build logs yet.'}</pre>
      {['QUEUED', 'BUILDING'].includes(workspace.revision.state) && <button className="btn btn-secondary" disabled={cancelling} onClick={cancel}>Cancel build</button>}
      <p>Editor Save and VS Code file Save update the live runtime. Save for Later publishes a durable revision. Save VS Code buffers and finish terminal commands before requesting a revision. After starting Save and Stop, avoid further edits; its snapshot captures one instant before the runtime ends.</p>
      {workspace.saved_revision && <p>Last durable revision: {workspace.saved_revision.revision_number} · <code>{workspace.saved_revision.image_digest_ref}</code></p>}
      {saveState && <p role="status">Save and Stop: {saveState}</p>}
      {runtime && <><p role="status">Runtime: {runtimeLabel(runtime.state)}</p>
        {runtime.failure_detail && <p role="alert">{runtime.failure_detail}</p>}
        {runtime.assigned_machine && <p>Assigned machine: {runtime.assigned_machine.display_name} · {runtime.assigned_machine.gpu_model ?? 'GPU'} · {runtime.assigned_machine.total_vram_gb?.toFixed(0) ?? '?'} GB VRAM</p>}
        {runtime.lifetime_deadline && <p>Runtime deadline: {runtime.lifetime_deadline}</p>}</>}
      {imageReady && (!runtime || ['STOPPED','FAILED'].includes(runtime.state)) && <>
        <h2>Request interactive access</h2>
        {history && <label>Revision to open<select value={selectedRevision ?? workspace.saved_revision_id ?? workspace.revision.id} onChange={(e) => setSelectedRevision(e.target.value)}>{history.items.filter((revision) => revision.state === 'IMAGE_READY').map((revision) => <option key={revision.id} value={revision.id}>Revision {revision.revision_number}{revision.is_saved_head ? ' (saved head)' : ''}</option>)}</select></label>}
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
        <p>Native Remote-SSH into the workload container (<code>dml</code>, <code>/workspace</code>). Save and Stop publishes a revision before ending SSH. Copy-paste on your machine:</p>
        <pre>{`dml-ssh configure ${runtime.id} --scheduler ${schedulerOrigin()}\n# VS Code: Remote-SSH: Connect to Host -> dml-${runtime.id}-g${(runtime as unknown as { ssh_generation?: number }).ssh_generation ?? runtime.generation}\n# Open folder /workspace`}</pre>
      </>}
      {runtime && !(runtime as unknown as { ssh_ready?: boolean }).ssh_ready && runtime.state === 'READY' && (runtime as unknown as { ssh_capable?: boolean }).ssh_capable !== true && <p role="status">SSH unavailable for this runtime (rebuild from an SSH-capable revision).</p>}
      {imageReady && !((runtime as unknown as { ssh_ready?: boolean } | null)?.ssh_ready && runtime?.state === 'READY') && <>
        <h2>VS Code Remote-SSH</h2>
        <p>Native Remote-SSH into the workload container (<code>dml</code>, <code>/workspace</code>) — same live files, Python environment and GPU as the browser editor. Requires VS Code with the Remote-SSH extension, an OpenSSH client, and the <code>dml-ssh</code> CLI (<code>pip install ./dml-ssh</code> from the repo). Copy-paste on your machine:</p>
        <pre>{`dml-ssh login --scheduler ${schedulerOrigin()}\ndml-ssh configure <runtime-id> --scheduler ${schedulerOrigin()}\n# VS Code: Remote-SSH: Connect to Host, then open /workspace`}</pre>
        {(!runtime || ['STOPPED', 'FAILED'].includes(runtime.state)) && <p role="status">Start a runtime above first — the exact connect command for your runtime appears here once it is READY and SSH-capable.</p>}
        {runtime && !['STOPPED', 'FAILED', 'READY'].includes(runtime.state) && <p role="status">Your runtime is still starting — the connect command appears here once it is READY and SSH-capable.</p>}
        {runtime?.state === 'READY' && <p role="status">This runtime is not SSH-capable: rebuild the image from an SSH-capable revision, then start a new runtime.</p>}
      </>}
      {runtime && !['STOPPED','FAILED'].includes(runtime.state) && <button className="btn btn-secondary" disabled={busy || runtime.desired_state === 'STOPPED'} onClick={stopRuntime}>{runtime.save_enabled ? 'Save and Stop' : 'Stop'}</button>}{' '}
      {runtime?.save_enabled && !['STOPPED','FAILED'].includes(runtime.state) && <button className="btn btn-secondary" disabled={busy || runtime.desired_state === 'STOPPED'} onClick={() => void discardRuntime()}>Discard changes and stop</button>}{' '}
      <button className="btn btn-secondary" disabled={busy || runtime?.state !== 'READY' || runtime.desired_state !== 'RUNNING' || connectionState === 'Checking connection…'} onClick={connect}>Connect</button>{' '}
      {runtime?.state === 'READY' && <Link className="btn" to={`/interactive/${id}/editor`}>Open Editor</Link>}{' '}
      {runtime && !runtime.editor_capable && <span title="Start a new editor-capable runtime after the workspace editor rollout is enabled">Editor unavailable for this runtime</span>}{' '}
      {history && <section><h2>Saved revision history</h2><ul>{history.items.map((revision) => <li key={revision.id}>Revision {revision.revision_number}: {revision.state}{revision.is_saved_head ? ' · current saved head' : ''} {revision.state === 'IMAGE_READY' && (!runtime || ['STOPPED', 'FAILED'].includes(runtime.state)) && <button className="btn btn-secondary" disabled={busy || !workspace.training_submission_enabled} onClick={() => { setTrainRevision(revision.id); setTrainSettings({ name: `${workspace.name} training`, command: 'python train.py', priority: 'NORMAL' }); }}>Train this revision</button>}</li>)}</ul></section>}
      {trainRevision && <section><h3>Train saved revision</h3><TrainingSettingsForm value={trainSettings} onChange={setTrainSettings} /><button className="btn" disabled={busy} onClick={() => void submitSavedRevision()}>Create training Job</button><button className="btn btn-secondary" onClick={() => setTrainRevision(null)}>Cancel</button></section>}
      {trainingJobId && <p role="status">Training Job created: <Link to={`/jobs/${trainingJobId}`}>Open Job</Link></p>}
      {connectionState && <p role="status">{connectionState}</p>}
    </>}
  </div>;
}
