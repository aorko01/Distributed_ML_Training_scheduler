import { useState } from 'react';
import { ChevronDown, ChevronRight, Copy, File, FilePlus, Folder, FolderPlus, RefreshCw, Trash2, Pencil, Loader2 } from 'lucide-react';
import type { ExplorerNode, WorkspaceSnapshot } from '../workspaceTypes';
import { baseName } from '../workspaceTypes';

export interface ExplorerOps {
  onToggleDir(path: string): void;
  onOpenFile(path: string): void;
  onRefreshDir(path: string): void;
  onRefreshRoot(): void;
  onCreateFile(dir: string): void;
  onCreateFolder(dir: string): void;
  onRename(path: string): void;
  onDelete(path: string): void;
  onCopyPath(path: string): void;
}

export function ExplorerPanel({ snap, wsName, ops, connected }: { snap: WorkspaceSnapshot; wsName: string; ops: ExplorerOps; connected: boolean }) {
  const [menu, setMenu] = useState<{ path: string; x: number; y: number } | null>(null);
  const rows: { node: ExplorerNode; depth: number }[] = [];
  const walk = (key: string, depth: number) => {
    for (const childKey of key === '' ? snap.roots : (snap.nodes[key]?.children ?? [])) {
      const node = snap.nodes[childKey];
      if (!node) continue;
      rows.push({ node, depth });
      if (node.type === 'directory' && node.expanded && node.children) walk(childKey, depth + 1);
    }
  };
  walk('', 0);
  return (
    <section className="ide-explorer" aria-label="Explorer">
      <div className="ide-pane-header">
        <span title={wsName}>EXPLORER \u00b7 {wsName}</span>
        <span className="ide-pane-actions">
          <button type="button" aria-label="New file" title="New file" disabled={!connected} onClick={() => ops.onCreateFile('')}><FilePlus size={15} /></button>
          <button type="button" aria-label="New folder" title="New folder" disabled={!connected} onClick={() => ops.onCreateFolder('')}><FolderPlus size={15} /></button>
          <button type="button" aria-label="Refresh explorer" title="Refresh explorer" disabled={!connected} onClick={ops.onRefreshRoot}><RefreshCw size={15} /></button>
        </span>
      </div>
      <div className="ide-tree" role="tree" aria-label="Workspace files" onClick={() => setMenu(null)}>
        {rows.length === 0 && <p className="ide-empty">No files yet. Use New file or the terminal.</p>}
        {rows.map(({ node, depth }) => (
          <div key={node.path} role="treeitem" aria-level={depth + 1} aria-expanded={node.type === 'directory' ? node.expanded : undefined} aria-selected={snap.selected === node.path} tabIndex={0}
            className={`ide-row${snap.selected === node.path ? ' selected' : ''}`} style={{ paddingLeft: 8 + depth * 14 }}
            onClick={(e) => { e.stopPropagation(); setMenu(null); if (node.type === 'directory') ops.onToggleDir(node.path); else if (node.type === 'file') ops.onOpenFile(node.path); }}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (node.type === 'directory') ops.onToggleDir(node.path); else if (node.type === 'file') ops.onOpenFile(node.path); } if (e.key === 'ArrowRight' && node.type === 'directory' && !node.expanded) ops.onToggleDir(node.path); if (e.key === 'ArrowLeft' && node.type === 'directory' && node.expanded) ops.onToggleDir(node.path); if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); const el = e.currentTarget as HTMLElement; const next = e.key === 'ArrowDown' ? el.nextElementSibling as HTMLElement | null : el.previousElementSibling as HTMLElement | null; next?.focus(); } }}
            onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); setMenu({ path: node.path, x: e.clientX, y: e.clientY }); }}
            title={node.path || '(root)'}>
            {node.type === 'directory' ? (node.expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />) : node.type === 'file' ? <File size={14} /> : <span aria-hidden="true">\u25c7</span>}
            <span className="ide-row-label">{node.path === '' ? wsName : baseName(node.path)}</span>
            {node.type !== 'file' && node.type !== 'directory' && <span className="ide-badge" title="Symlinks and special files cannot be opened in the editor">link</span>}
            {node.loading && <Loader2 size={13} className="ide-spin" />}
            {node.error && <span className="ide-row-error" role="alert" title={node.error}>!</span>}
            {snap.tabs.includes(node.path) && <span className="ide-dot" title="Open in editor">\u2022</span>}
          </div>
        ))}
      </div>
      {menu && (
        <div className="ide-menu" role="menu" style={{ left: Math.min(menu.x, window.innerWidth - 220), top: Math.min(menu.y, window.innerHeight - 260) }} onMouseLeave={() => setMenu(null)}>
          <button type="button" role="menuitem" onClick={() => { ops.onOpenFile(menu.path); setMenu(null); }}><File size={13} /> Open</button>
          <button type="button" role="menuitem" onClick={() => { ops.onCopyPath(menu.path); setMenu(null); }}><Copy size={13} /> Copy relative path</button>
          <button type="button" role="menuitem" onClick={() => { ops.onRefreshDir(menu.path); setMenu(null); }}><RefreshCw size={13} /> Refresh</button>
          <button type="button" role="menuitem" onClick={() => { ops.onRename(menu.path); setMenu(null); }}><Pencil size={13} /> Rename</button>
          <button type="button" role="menuitem" onClick={() => { ops.onCreateFile(menu.path); setMenu(null); }}><FilePlus size={13} /> New file here</button>
          <button type="button" role="menuitem" onClick={() => { ops.onCreateFolder(menu.path); setMenu(null); }}><Folder size={13} /> New folder here</button>
          <button type="button" role="menuitem" className="danger" onClick={() => { ops.onDelete(menu.path); setMenu(null); }}><Trash2 size={13} /> Delete…</button>
        </div>
      )}
    </section>
  );
}
