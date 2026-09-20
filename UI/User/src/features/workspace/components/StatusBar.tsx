import type { ConnPhase, PtyUiState } from '../workspaceTypes';
export function StatusBar({ phase, pty, dirtyCount, active, lineCol, readOnly, connected }: { phase: ConnPhase; pty: PtyUiState; dirtyCount: number; active: string | null; lineCol: string; readOnly: boolean; connected: boolean }) {
  return (
    <footer className="ide-statusbar" role="status" aria-label="Workspace status">
      <span title="Git branch (static for v1)">main</span>
      <span title={connected ? 'Connected' : `Connection: ${phase}`}>{connected ? '● Connected' : `○ ${phase}`}</span>
      <span title={`Terminal: ${pty}`}>Terminal: {pty}</span>
      {readOnly && <span title="Workspace is read-only">read-only</span>}
      <span className="ide-status-spacer" />
      {active && <span title={active}>{active}</span>}
      <span title="Cursor position">{lineCol}</span>
      <span>Spaces: 2</span><span>UTF-8</span>
      <span title={dirtyCount ? `${dirtyCount} unsaved file(s)` : 'No unsaved changes'}>{dirtyCount ? `${dirtyCount} unsaved` : 'saved'}</span>
    </footer>
  );
}
