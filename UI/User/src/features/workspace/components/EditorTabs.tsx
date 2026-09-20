import { X } from 'lucide-react';
import { baseName, parentPath } from '../workspaceTypes';
export function EditorTabs({ tabs, active, dirty, onActivate, onClose, onCloseOthers }: { tabs: string[]; active: string | null; dirty: (p: string) => boolean; onActivate: (p: string) => void; onClose: (p: string) => void; onCloseOthers: (p: string) => void }) {
  const dupes = new Set(tabs.filter((t, i) => tabs.findIndex((x) => baseName(x) === baseName(t)) !== i).map((t) => baseName(t)));
  return (
    <div className="ide-tabs" role="tablist" aria-label="Open files">
      {tabs.map((t) => (
        <div key={t} role="tab" aria-selected={active === t} tabIndex={0} title={`${t}`} className={`ide-tab${active === t ? ' active' : ''}`} onClick={() => onActivate(t)} onAuxClick={(e) => { if (e.button === 1) onClose(t); }} onKeyDown={(e) => { if (e.key === 'Enter') onActivate(t); if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { e.preventDefault(); const i = tabs.indexOf(t); const n = e.key === 'ArrowRight' ? tabs[(i + 1) % tabs.length] : tabs[(i - 1 + tabs.length) % tabs.length]; if (n) onActivate(n); } }} onContextMenu={(e) => { e.preventDefault(); onCloseOthers(t); }}>
          <span className="ide-tab-name">{baseName(t)}{dupes.has(baseName(t)) ? ` · ${parentPath(t)}` : ''}</span>
          {dirty(t) ? <span className="ide-dirty" title="Unsaved changes" aria-label="Unsaved changes">●</span> : null}
          <button type="button" className="ide-tab-close" aria-label={`Close ${t}`} title={`Close ${t}`} onClick={(e) => { e.stopPropagation(); onClose(t); }}><X size={13} /></button>
        </div>
      ))}
      {tabs.length === 0 && <span className="ide-tabs-empty">No open files — pick a file from the explorer.</span>}
    </div>
  );
}
