import type { OpenFile, WorkspaceSnapshot } from './workspaceTypes';

export type Action =
  | { type: 'loaded'; workspace: WorkspaceSnapshot['workspace']; runtime: WorkspaceSnapshot['runtime'] }
  | { type: 'phase'; phase: WorkspaceSnapshot['phase']; message?: string; closeCode?: number | null }
  | { type: 'attempt'; attempt: number }
  | { type: 'children'; dir: string; entries: { name: string; type: string }[]; nextCursor: number | null; append: boolean }
  | { type: 'dirLoading'; dir: string; loading: boolean; error?: string | null }
  | { type: 'toggle'; dir: string }
  | { type: 'select'; path: string | null }
  | { type: 'opened'; path: string; version: string; text: string }
  | { type: 'saved'; path: string; version: string; text: string }
  | { type: 'saving'; path: string; saving: boolean; error?: string | null }
  | { type: 'fileError'; path: string; error: string | null }
  | { type: 'conflict'; path: string; serverText: string; serverVersion: string }
  | { type: 'resolveConflict'; path: string; keep: 'server' | 'local'; serverText?: string; serverVersion?: string }
  | { type: 'activate'; path: string }
  | { type: 'closedTab'; path: string }
  | { type: 'renamed'; oldPath: string; newPath: string }
  | { type: 'removed'; path: string }
  | { type: 'pty'; state: WorkspaceSnapshot['pty']; exit?: { code: number; reason: string } | null }
  | { type: 'ptyActive'; active: boolean }
  | { type: 'notice'; notice: WorkspaceSnapshot['notices'][number] }
  | { type: 'dismissNotice'; id: string }
  | { type: 'readOnly'; readOnly: boolean }
  | { type: 'caps'; caps: WorkspaceSnapshot['caps'] }
  | { type: 'edited'; texts: Record<string, string> };

export function initialSnapshot(): WorkspaceSnapshot {
  return { workspace: null, runtime: null, phase: 'idle', message: 'Connecting…', closeCode: null, attempt: 0, ptyHadSession: false, nodes: {}, roots: [], selected: null, tabs: [], active: null, files: {}, dirtyCount: 0, readOnly: false, pty: 'closed', ptyExit: null, notices: [], conflictPath: null, caps: null };
}
function normType(t: string): 'file' | 'directory' | 'symlink' | 'unsupported' { return t === 'directory' || t === 'file' || t === 'symlink' ? t : 'unsupported'; }
export function countDirty(files: Record<string, OpenFile>, texts?: Record<string, string>): number { const t = texts ?? {}; return Object.values(files).filter((f) => (t[f.path] ?? f.savedText) !== f.savedText).length; }

// Stable explorer order: directories first, then files, case-insensitive with
// deterministic tie-breaks (instruction §9).
export function sortEntries(entries: { name: string; type: string }[]): { name: string; type: string }[] {
  const rank = (t: string): number => (t === 'directory' ? 0 : t === 'file' ? 1 : 2);
  return [...entries].sort((a, b) => {
    const r = rank(a.type) - rank(b.type);
    if (r !== 0) return r;
    const c = a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
    if (c !== 0) return c;
    return a.name < b.name ? -1 : a.name > b.name ? 1 : 0;
  });
}

export function reducer(prev: WorkspaceSnapshot, action: Action): WorkspaceSnapshot {
  switch (action.type) {
    case 'loaded': return { ...prev, workspace: action.workspace, runtime: action.runtime };
    case 'phase': return { ...prev, phase: action.phase, message: action.message ?? prev.message, closeCode: action.closeCode === undefined ? prev.closeCode : action.closeCode };
    case 'attempt': return { ...prev, attempt: action.attempt };
    case 'readOnly': return { ...prev, readOnly: action.readOnly };
    case 'caps': return { ...prev, caps: action.caps };
    case 'pty': return { ...prev, pty: action.state, ptyExit: action.exit === undefined ? prev.ptyExit : action.exit };
    case 'ptyActive': return { ...prev, ptyHadSession: action.active };
    case 'notice': return { ...prev, notices: [...prev.notices.slice(-4), action.notice] };
    case 'dismissNotice': return { ...prev, notices: prev.notices.filter((n) => n.id !== action.id) };
    case 'select': return { ...prev, selected: action.path };
    case 'toggle': { const n = prev.nodes[action.dir]; if (!n) return prev; return { ...prev, nodes: { ...prev.nodes, [action.dir]: { ...n, expanded: !n.expanded } } }; }
    case 'dirLoading': { const n = prev.nodes[action.dir]; const base = n ?? { path: action.dir, name: action.dir, type: 'directory' as const, expanded: true, loading: false, error: null, children: null, nextCursor: null }; return { ...prev, nodes: { ...prev.nodes, [action.dir]: { ...base, loading: action.loading, error: action.error ?? (action.loading ? null : base.error) } } }; }
    case 'children': {
      const dirKey = action.dir;
      const nodes = { ...prev.nodes };
      const dirNode = nodes[dirKey] ?? { path: dirKey, name: dirKey.split('/').pop() ?? dirKey, type: 'directory' as const, expanded: true, loading: false, error: null, children: null, nextCursor: null };
      const prevKids = (action.append ? dirNode.children ?? [] : []);
      const kids: string[] = [...prevKids];
      for (const e of sortEntries(action.entries)) {
        const p = dirKey ? `${dirKey}/${e.name}` : e.name;
        nodes[p] = { path: p, name: e.name, type: normType(e.type), expanded: false, loading: false, error: null, children: e.type === 'directory' ? [] : null, nextCursor: null };
        if (!kids.includes(p)) kids.push(p);
      }
      nodes[dirKey] = { ...dirNode, expanded: true, loading: false, error: null, children: kids, nextCursor: action.nextCursor };
      const roots = dirKey === '' ? kids : prev.roots;
      return { ...prev, nodes, roots };
    }
    case 'opened': {
      const files = { ...prev.files, [action.path]: { path: action.path, version: action.version, savedText: action.text, conflict: null, saving: false, error: null } };
      const tabs = prev.tabs.includes(action.path) ? prev.tabs : [...prev.tabs, action.path];
      return { ...prev, files, tabs, active: action.path, selected: action.path };
    }
    case 'saved': {
      const f = prev.files[action.path]; if (!f) return prev;
      const files = { ...prev.files, [action.path]: { ...f, version: action.version, savedText: action.text, conflict: null, saving: false, error: null } };
      return { ...prev, files, dirtyCount: prev.dirtyCount };
    }
    case 'saving': { const f = prev.files[action.path]; if (!f) return prev; return { ...prev, files: { ...prev.files, [action.path]: { ...f, saving: action.saving, error: action.error ?? null } } }; }
    case 'fileError': { const f = prev.files[action.path]; if (!f) return prev; return { ...prev, files: { ...prev.files, [action.path]: { ...f, error: action.error } } }; }
    case 'conflict': { const f = prev.files[action.path]; if (!f) return prev; return { ...prev, files: { ...prev.files, [action.path]: { ...f, saving: false, conflict: { serverText: action.serverText, serverVersion: action.serverVersion } } }, conflictPath: action.path }; }
    case 'resolveConflict': {
      const f = prev.files[action.path]; if (!f) return { ...prev, conflictPath: null };
      if (action.keep === 'server') { const files = { ...prev.files, [action.path]: { ...f, version: action.serverVersion ?? f.version, savedText: action.serverText ?? f.savedText, conflict: null } }; return { ...prev, files, conflictPath: null, dirtyCount: countDirty(files) }; }
      return { ...prev, files: { ...prev.files, [action.path]: { ...f, conflict: null } }, conflictPath: null };
    }
    case 'activate': return { ...prev, active: action.path, selected: action.path };
    case 'closedTab': {
      const tabs = prev.tabs.filter((t) => t !== action.path);
      const files = { ...prev.files }; delete files[action.path];
      const active = prev.active === action.path ? (tabs[tabs.length - 1] ?? null) : prev.active;
      return { ...prev, tabs, files, active, conflictPath: prev.conflictPath === action.path ? null : prev.conflictPath, dirtyCount: countDirty(files) };
    }
    case 'renamed': {
      const nodes = { ...prev.nodes }; delete nodes[action.oldPath];
      const tabs = prev.tabs.map((t) => (t === action.oldPath ? action.newPath : t.startsWith(action.oldPath + '/') ? action.newPath + t.slice(action.oldPath.length) : t));
      const files: Record<string, OpenFile> = {};
      for (const [k, v] of Object.entries(prev.files)) { const nk = k === action.oldPath ? action.newPath : k.startsWith(action.oldPath + '/') ? action.newPath + k.slice(action.oldPath.length) : k; files[nk] = { ...v, path: nk }; }
      return { ...prev, nodes, tabs, files, active: prev.active === action.oldPath ? action.newPath : prev.active, dirtyCount: countDirty(files) };
    }
    case 'removed': {
      const nodes = { ...prev.nodes }; for (const k of Object.keys(nodes)) { if (k === action.path || k.startsWith(action.path + '/')) delete nodes[k]; }
      const tabs = prev.tabs.filter((t) => t !== action.path && !t.startsWith(action.path + '/'));
      const files: Record<string, OpenFile> = {}; for (const [k, v] of Object.entries(prev.files)) { if (k !== action.path && !k.startsWith(action.path + '/')) files[k] = v; }
      return { ...prev, nodes, tabs, files, active: tabs.includes(prev.active ?? '') ? prev.active : (tabs[tabs.length - 1] ?? null), dirtyCount: countDirty(files) };
    }
    case 'edited': return { ...prev, dirtyCount: countDirty(prev.files, action.texts) };
  }
}
