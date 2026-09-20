import { Files, Search, Settings } from 'lucide-react';
export function ActivityBar({ explorerOpen, onToggleExplorer, onToggleTerminal, terminalCollapsed }: { explorerOpen: boolean; onToggleExplorer: () => void; onToggleTerminal: () => void; terminalCollapsed: boolean }) {
  return (
    <div className="ide-activity" role="toolbar" aria-label="IDE views">
      <button type="button" className={explorerOpen ? 'active' : ''} aria-label="Toggle explorer" title="Toggle explorer (Ctrl/Cmd+B)" onClick={onToggleExplorer}><Files size={19} /></button>
      <button type="button" aria-label="Search in current file" title="Search in current file (Ctrl/Cmd+F)" onClick={() => document.querySelector<HTMLTextAreaElement>('.ide-editor textarea')?.focus()}><Search size={19} /></button>
      <span className="ide-activity-spacer" />
      <button type="button" className={terminalCollapsed ? '' : 'active'} aria-label="Toggle terminal" title="Toggle terminal (Ctrl+`)" onClick={onToggleTerminal}><Settings size={19} /></button>
    </div>
  );
}
