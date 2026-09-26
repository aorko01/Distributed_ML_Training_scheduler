import { useEffect, useState, useRef } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, Boxes, CheckCircle2, Cpu, Loader2, MonitorPlay, SquarePen, TerminalSquare } from 'lucide-react';
import { interactive, interactiveCapacity, type Workspace, type Runtime, type CapacityOptions, type ResourceRequirements } from '../services/interactive';
import { schedulerOrigin } from '../services/api';
import { ResourceRequirementsForm } from '../features/interactive-capacity/ResourceRequirementsForm';
import { CapacitySummary } from '../features/interactive-capacity/CapacitySummary';
import { MachineGrid } from '../features/interactive-capacity/MachineGrid';
import { useCapacityPreview } from '../features/interactive-capacity/useCapacityPreview';
import { normalizeRequirements, requirementsValid } from '../features/interactive-capacity/requirements';
import CopyButton from '../components/CopyButton';

import { verifyConnection } from '../services/terminalVerification';

function runtimeLabel(state: string): string {
  if (state === 'QUEUED') return 'Queued';
  if (['ASSIGNED', 'PULLING', 'STARTING', 'CONNECTING'].includes(state)) return `Starting (${state.toLowerCase()})`;
  if (state === 'READY') return 'Live';
  return state.toLowerCase();
}

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
  const [copyError, setCopyError] = useState('');
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
  const building = workspace && ['QUEUED', 'BUILDING'].includes(workspace.revision.state);
  const canRequest = imageReady && (!runtime || ['STOPPED', 'FAILED'].includes(runtime.state)) && valid && !!requirements;
  const noCapable = preview && preview.matching_online === 0;
  const allBusy = preview && preview.matching_online > 0 && preview.available_now === 0;
  const ready = runtime?.state === 'READY';
  const sshReady = (runtime as unknown as { ssh_ready?: boolean } | null)?.ssh_ready && ready;
  const sshAlias = runtime
    ? `dml-${runtime.id}-g${(runtime as unknown as { ssh_generation?: number }).ssh_generation ?? runtime.generation}`
    : '';
  const sshCmd = runtime
    ? `dml-ssh configure ${runtime.id} --scheduler ${schedulerOrigin()}\ncode --folder-uri vscode-remote://ssh-remote+${sshAlias}/workspace`
    : '';
  const assignedJobs = preview?.machines.reduce((n, m) => n + m.workloads.length, 0) ?? 0;

  return (
    <div className="fade-in iw-detail">
      <Link to="/interactive" className="iw-back"><ArrowLeft size={15} /> Sessions</Link>

      {error && <div className="card training-error" role="alert">{error}<div style={{ marginTop: '0.6rem' }}><button className="btn btn-secondary" onClick={() => setReload(v => v + 1)}>Retry</button></div></div>}
      {!workspace && !error && <div style={{ display: 'flex', justifyContent: 'center', padding: '3rem' }}><Loader2 className="animate-spin" size={30} /></div>}

      {workspace && (
        <>
          <div className="builds-hero iw-hero">
            <div>
              <span className="ws-eyebrow"><MonitorPlay size={14} /> Session · Rev {workspace.revision.revision_number}</span>
              <h1>{workspace.name}</h1>
              <div className="iw-card-badges">
                <span className={imageBadge(workspace.revision.state)}>
                  {workspace.revision.state === 'IMAGE_READY' ? 'Image ready' : workspace.revision.state.toLowerCase()}
                </span>
                {runtime && <span className="badge badge-running">{runtimeLabel(runtime.state)}</span>}
                {runtime?.assigned_machine && <span className="iw-machine-tag">{runtime.assigned_machine.display_name}</span>}
              </div>
            </div>
            <div className="iw-hero-actions">
              {ready && (
                <Link className="btn btn-primary" to={`/interactive/${id}/editor`}><SquarePen size={16} /> Open editor</Link>
              )}
              {liveRuntime && (
                <button className="btn btn-secondary" disabled={busy || runtime?.desired_state === 'STOPPED'} onClick={stopRuntime}>Stop</button>
              )}
            </div>
          </div>

          {/* Step 1 — image */}
          <section className="card iw-step">
            <header className="iw-step-head">
              <span className="iw-step-no">1</span>
              <div>
                <h2>Image</h2>
                <p className="iw-muted">
                  {workspace.source_type === 'UPLOAD' ? 'Uploaded workspace' : 'From existing build'}
                  {workspace.revision.requested_base_image ? <> · <code>{workspace.revision.requested_base_image}</code></> : null}
                  {workspace.source_type !== 'UPLOAD' && workspace.revision.source_image_tag ? <> · <code>{workspace.revision.source_image_tag}</code></> : null}
                </p>
              </div>
              <span className="iw-step-state">{building ? 'Building…' : imageReady ? 'Ready' : workspace.revision.state.toLowerCase()}</span>
            </header>
            {workspace.revision.image_tag && <p className="iw-mono">Tag <code>{workspace.revision.image_tag}</code></p>}
            {workspace.revision.failure_reason && <p role="alert" className="error-text">{workspace.revision.failure_reason}</p>}
            <details className="iw-logs">
              <summary>Build logs</summary>
              <pre aria-label="Build logs">{lines.join('\n') || 'No build logs yet.'}</pre>
            </details>
            {building && <div><button className="btn btn-secondary" disabled={cancelling} onClick={cancel}>Cancel build</button></div>}
          </section>

          {/* Step 2 — machine */}
          {imageReady && (!runtime || ['STOPPED', 'FAILED'].includes(runtime.state)) && (
            <section className="card iw-step">
              <header className="iw-step-head">
                <span className="iw-step-no">2</span>
                <div>
                  <h2>Machine</h2>
                  <p className="iw-muted">Set minimums — only matching machines are listed, with current load.</p>
                </div>
              </header>
              {requirements && <ResourceRequirementsForm value={requirements} options={capOptions} onChange={setRequirements} />}
              {!valid && <p role="alert" className="error-text">Requirements are outside operator bounds.</p>}
              <div style={{ marginTop: '1rem' }}>
                <CapacitySummary preview={preview} loading={previewLoading} />
                {previewError && <p role="alert" className="error-text">{previewError}</p>}
                {!previewLoading && preview && (
                  <p className="iw-muted" style={{ marginTop: '0.5rem' }}>
                    {preview.matching_online} eligible machine{preview.matching_online === 1 ? '' : 's'} · {assignedJobs} job{assignedJobs === 1 ? '' : 's'} running on them
                  </p>
                )}
              </div>
              <div style={{ marginTop: '1rem' }}>
                <MachineGrid machines={preview?.machines ?? []} />
              </div>
              {noCapable && <p role="status" className="iw-muted">No capable machine online — lower the minimums or wait.</p>}
              {allBusy && <p role="status" className="iw-muted">Matching machines are busy — you can queue and we’ll assign one when free.</p>}
              <div className="iw-create-foot">
                <span className="iw-muted">Runtime is temporary — Stop discards unsaved changes.</span>
                <button className="btn btn-primary" disabled={busy || !canRequest || !!noCapable} onClick={startRuntime}>
                  {busy ? 'Requesting…' : allBusy ? 'Queue on a machine' : 'Get a machine'}
                </button>
              </div>
            </section>
          )}

          {/* Step 3 — session */}
          {liveRuntime && (
            <section className="card iw-step">
              <header className="iw-step-head">
                <span className="iw-step-no">3</span>
                <div>
                  <h2>Session {ready ? 'live' : runtimeLabel(runtime!.state)}</h2>
                  <p className="iw-muted">
                    {runtime?.assigned_machine
                      ? <>{runtime.assigned_machine.display_name} · {runtime.assigned_machine.gpu_model ?? 'GPU'} · {runtime.assigned_machine.total_vram_gb?.toFixed(0) ?? '?'} GB VRAM</>
                      : 'Waiting for a matching machine…'}
                    {runtime?.lifetime_deadline ? <> · ends {new Date(runtime.lifetime_deadline).toLocaleString()}</> : null}
                  </p>
                </div>
              </header>
              {runtime?.failure_detail && <p role="alert" className="error-text">{runtime.failure_detail}</p>}

              <div className="iw-connect-grid">
                <div className="iw-connect-card">
                  <TerminalSquare size={18} />
                  <div>
                    <strong>Browser editor</strong>
                    <p>Same files, environment and GPU — no setup.</p>
                    {ready
                      ? <Link className="btn btn-primary" to={`/interactive/${id}/editor`}><SquarePen size={15} /> Open editor</Link>
                      : <p className="iw-muted">Editor unlocks once the session is live.</p>}
                    {runtime && !runtime.editor_capable && <p className="iw-muted">Editor unavailable for this runtime.</p>}
                  </div>
                </div>
                <div className="iw-connect-card">
                  <Cpu size={18} />
                  <div>
                    <strong>VS Code Remote-SSH</strong>
                    <p>Paste in your terminal to open <code>/workspace</code> in VS Code. It uses the same files and Python environment as the browser editor.</p>
                    {sshReady ? (
                      <>
                        <pre className="iw-cmd">{sshCmd}</pre>
                        <div className="iw-cmd-row">
                          <CopyButton value={sshCmd} label="Copy command" onResult={(ok) => setCopyError(ok ? '' : 'Copy failed — select the command manually.')} />
                        </div>
                        {copyError && <p role="alert" className="error-text">{copyError}</p>}
                      </>
                    ) : (
                      <details className="iw-logs">
                        <summary>How it works</summary>
                        <pre className="iw-cmd">{`dml-ssh login --scheduler ${schedulerOrigin()}\ndml-ssh configure <runtime-id> --scheduler ${schedulerOrigin()}\ncode --folder-uri vscode-remote://ssh-remote+<configured-host>/workspace`}</pre>
                        <p className="iw-muted">Needs VS Code + Remote-SSH, an OpenSSH client and the <code>dml-ssh</code> CLI. The exact command for this session appears here once it’s live{runtime && !['STOPPED', 'FAILED', 'READY'].includes(runtime.state) ? ' (still starting…)' : ''}.</p>
                      </details>
                    )}
                  </div>
                </div>
              </div>

              <div className="iw-session-foot">
                <button className="btn btn-secondary" disabled={busy || runtime?.desired_state === 'STOPPED'} onClick={stopRuntime}>Stop session</button>
                <button
                  className="btn btn-secondary"
                  disabled={busy || runtime?.state !== 'READY' || runtime.desired_state !== 'RUNNING' || connectionState === 'Checking connection…'}
                  onClick={connect}
                >
                  Check connection
                </button>
                <button className="btn btn-secondary" disabled title="Saving is not available in this phase">Save as new revision</button>
                {connectionState && <span role="status" className="iw-muted">{connectionState}</span>}
              </div>
              <p className="iw-muted"><CheckCircle2 size={13} style={{ verticalAlign: '-2px' }} /> Live only — Stop ends browser + SSH access immediately.</p>
            </section>
          )}

          {imageReady && !liveRuntime && (runtime as unknown as { ssh_ready?: boolean } | null) !== null && runtime && ['STOPPED', 'FAILED'].includes(runtime.state) && (
            <p className="iw-muted"><Boxes size={13} style={{ verticalAlign: '-2px' }} /> Last session ended — pick a machine above to start again.</p>
          )}
        </>
      )}
    </div>
  );
}
