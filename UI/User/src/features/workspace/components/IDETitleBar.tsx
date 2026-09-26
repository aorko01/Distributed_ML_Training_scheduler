import { Link } from 'react-router-dom';
import { ArrowLeft, Save, Wifi, WifiOff } from 'lucide-react';
import type { ConnPhase } from '../workspaceTypes';
export function IDETitleBar({ workspaceId, name, runtimeState, liveOnly, phase, dirtyCount, saving, onSaveAll, onReconnect, onStop, internetEnabled, packageCapable, developerMode, submitEnabled, onSubmitTraining }: { workspaceId: string; name: string; runtimeState: string; liveOnly: boolean; phase: ConnPhase; dirtyCount: number; saving: boolean; onSaveAll: () => void; onReconnect: () => void; onStop: () => void; internetEnabled?: boolean; packageCapable?: boolean; developerMode?: boolean; submitEnabled?: boolean; onSubmitTraining?: () => void }) {
  const connected = phase === 'connected';
  const internetTitle = internetEnabled === true
    ? 'Operator-enabled egress for this runtime: pip install, datasets and curl work in the web terminal'
    : internetEnabled === false
      ? 'Internet disabled by operator: pip install and dataset downloads fail with DNS errors'
      : 'Internet capability unknown';
  const packageTitle = packageCapable === true
    ? 'Package-capable developer runtime: try `pip install six` and `sudo apt-get install ffmpeg`, then `python train.py`'
    : developerMode === true && internetEnabled !== true
      ? 'Developer runtime without network for this runtime: sudo works but pip downloads fail'
      : 'Packages need a new workspace image built with the developer profile and operator package gates';
  const submitTitle = submitEnabled === true
    ? 'Create a new batch job from the published image; live container changes are not included'
    : 'Published workspace image is not ready for training';
  return (
    <header className="ide-titlebar">
      <Link className="ide-back" to={`/interactive/${workspaceId}`} aria-label="Back to workspace details" title="Back to workspace details"><ArrowLeft size={16} /><span>Workspaces</span></Link>
      <span className="ide-wsname" title={name}>{name}</span>
      <span className={`ide-pill ${connected ? 'ide-pill-ready' : 'ide-pill-bad'}`} role="status" title={connected ? 'Connected to workspace' : 'Workspace connection state'}>{connected ? <Wifi size={13} /> : <WifiOff size={13} />} {connected ? 'READY' : runtimeState || phase}</span>
      {liveOnly && <span className="ide-pill ide-pill-warn" title="Writes go to the running container only and disappear if the runtime stops">live-only</span>}
      {dirtyCount > 0 && <span className="ide-pill ide-pill-dirty" role="status" title={`${dirtyCount} file(s) with unsaved changes`}>{dirtyCount} unsaved</span>}
      {internetEnabled !== undefined && (
        <span className={`ide-pill ${internetEnabled ? 'ide-pill-ready' : 'ide-pill-bad'}`} role="status" title={internetTitle}>
          {internetEnabled ? <Wifi size={13} /> : <WifiOff size={13} />} {internetEnabled ? 'online' : 'offline'}
        </span>
      )}
      {packageCapable !== undefined && (
        <span className={`ide-pill ${packageCapable ? 'ide-pill-ready' : 'ide-pill-bad'}`} role="status" title={packageTitle}>
          {packageCapable ? 'packages ready' : 'packages unavailable'}
        </span>
      )}
      <span className="ide-spacer" />
      {phase !== 'connected' && phase !== 'loading' && phase !== 'connecting' && <button type="button" className="ide-btn ide-btn-primary" onClick={onReconnect}>Reconnect</button>}
      <button type="button" className="ide-btn" onClick={onSaveAll} disabled={!connected || dirtyCount === 0 || saving} title="Save all dirty files (Ctrl/Cmd+Shift+S)"><Save size={14} /> Save All</button>
      <button type="button" className="ide-btn" disabled={!connected || submitEnabled !== true || !onSubmitTraining} onClick={onSubmitTraining} title={submitTitle}>Submit for Training</button>
      <button type="button" className="ide-btn ide-btn-danger-ghost" onClick={onStop} title="Stop runtime (live container changes may disappear)">Stop</button>
    </header>
  );
}
