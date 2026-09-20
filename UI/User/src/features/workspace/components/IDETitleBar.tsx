import { Link } from 'react-router-dom';
import { ArrowLeft, Save, Wifi, WifiOff } from 'lucide-react';
import type { ConnPhase } from '../workspaceTypes';
export function IDETitleBar({ workspaceId, name, runtimeState, liveOnly, phase, dirtyCount, saving, onSaveAll, onReconnect, onStop }: { workspaceId: string; name: string; runtimeState: string; liveOnly: boolean; phase: ConnPhase; dirtyCount: number; saving: boolean; onSaveAll: () => void; onReconnect: () => void; onStop: () => void }) {
  const connected = phase === 'connected';
  return (
    <header className="ide-titlebar">
      <Link className="ide-back" to={`/interactive/${workspaceId}`} aria-label="Back to workspace details" title="Back to workspace details"><ArrowLeft size={16} /><span>Workspaces</span></Link>
      <span className="ide-wsname" title={name}>{name}</span>
      <span className={`ide-pill ${connected ? 'ide-pill-ready' : 'ide-pill-bad'}`} role="status" title={connected ? 'Connected to workspace' : 'Workspace connection state'}>{connected ? <Wifi size={13} /> : <WifiOff size={13} />} {connected ? 'READY' : runtimeState || phase}</span>
      {liveOnly && <span className="ide-pill ide-pill-warn" title="Writes go to the running container only and disappear if the runtime stops">live-only</span>}
      {dirtyCount > 0 && <span className="ide-pill ide-pill-dirty" role="status" title={`${dirtyCount} file(s) with unsaved changes`}>{dirtyCount} unsaved</span>}
      <span className="ide-spacer" />
      {phase !== 'connected' && phase !== 'loading' && phase !== 'connecting' && <button type="button" className="ide-btn ide-btn-primary" onClick={onReconnect}>Reconnect</button>}
      <button type="button" className="ide-btn" onClick={onSaveAll} disabled={!connected || dirtyCount === 0 || saving} title="Save all dirty files (Ctrl/Cmd+Shift+S)"><Save size={14} /> Save All</button>
      <button type="button" className="ide-btn" disabled title="Durable snapshot publishing is not yet available on this deployment">Save for Later</button>
      <button type="button" className="ide-btn" disabled title="Training handoff is not yet available on this deployment">Submit for Training</button>
      <button type="button" className="ide-btn ide-btn-danger-ghost" onClick={onStop} title="Stop runtime (live container changes may disappear)">Stop</button>
    </header>
  );
}
