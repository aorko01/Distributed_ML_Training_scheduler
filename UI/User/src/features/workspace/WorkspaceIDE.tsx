import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { interactive } from '../../services/interactive';
import { WorkspaceConnection } from '../../services/workspaceProtocol';
import { initialSnapshot, reducer } from './workspaceReducer';
import { baseName, isValidName, joinPath, parentPath } from './workspaceTypes';
import { useEditorModels } from './hooks/useEditorModels';
import { useWorkspaceConnection } from './hooks/useWorkspaceConnection';
import { useBeforeUnloadDirtyGuard, useRouteDirtyGuard } from './hooks/useBeforeUnloadDirtyGuard';
import { useResizablePanels } from './hooks/useResizablePanels';
import { IDETitleBar } from './components/IDETitleBar';
import { ActivityBar } from './components/ActivityBar';
import { ExplorerPanel } from './components/ExplorerPanel';
import { EditorTabs } from './components/EditorTabs';
import { EditorPane } from './components/EditorPane';
import { TerminalPanel } from './components/TerminalPanel';
import { StatusBar } from './components/StatusBar';
import { Dialog } from './components/Dialog';
import { ToastRegion } from './components/ToastRegion';

let noticeSeq = 0;
const notice = (kind: 'info' | 'success' | 'error', text: string) => ({ id: `n${++noticeSeq}`, kind, text });

export default function WorkspaceIDE() {
  const { id } = useParams();
  const [snap, dispatch] = useReducer(reducer, undefined, initialSnapshot);
  const [monacoFactory, setMonacoFactory] = useState<undefined | ((path: string, initialText: string) => { getValue(): string; setValue(v: string): void; dispose(): void; subscribe(cb: () => void): () => void })>(undefined);
  const models = useEditorModels(monacoFactory);
  // Load Monaco lazily in real browsers only; jsdom/component tests keep the
  // in-memory model registry through the same adapter interface.
  useEffect(() => {
    let cancelled = false;
    const ua = typeof navigator === 'undefined' ? '' : navigator.userAgent;
    if (ua.includes('jsdom') || typeof window === 'undefined') return;
    void import('./monacoAdapter').then((m) => {
      if (cancelled) return;
      const runtimeId = snapRef.current.runtime?.id ?? 'workspace';
      setMonacoFactory(() => (path: string, initialText: string) => m.acquireMonacoModel(runtimeId, path, initialText));
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, []);
  const [editorText, setEditorText] = useState('');
  const [lineCol, setLineCol] = useState('Ln 1, Col 1');
  const [savingAll, setSavingAll] = useState(false);
  const [maxTerm, setMaxTerm] = useState(false);
  const [prompt, setPrompt] = useState<null | { kind: 'createFile' | 'createFolder' | 'rename' | 'delete' | 'closeDirty' | 'conflict' | 'stop'; dir?: string; path?: string }>(null);
  const [promptValue, setPromptValue] = useState('');
  const connRef = useRef<WorkspaceConnection | null>(null);
  const termLines = useRef<{ write(d: Uint8Array | string): void; clear(): void } | null>(null);
  const termHandle = useRef<{ write(d: Uint8Array | string): void; clear(): void; focus(): void; cols(): number; rows(): number; paste(t: string): void } | null>(null);
  const panels = useResizablePanels();
  // Stable terminal callbacks: TerminalPanel mounts its xterm surface once
  // (effect deps [collapsed] only). Inline closures here would tear the
  // surface down on every keystroke and drop PTY output.
  const registerTerminal = useCallback((h: { write(d: Uint8Array | string): void; clear(): void; focus(): void; cols(): number; rows(): number; paste(t: string): void } | null) => {
    termHandle.current = h;
    termLines.current = h;
  }, []);
  const handleTermInput = useCallback((data: string) => { connRef.current?.ptyInput(data); }, []);
  const handleTermResize = useCallback((c: number, r: number) => { connRef.current?.resize(c, r); }, []);
  const { setTerminalHeight } = panels;
  const handleTermHeight = useCallback((h: number) => { setTerminalHeight(h); }, [setTerminalHeight]);
  // One-shot restart: the "+" button while a shell is running closes it and
  // reopens a fresh shell once the backend acknowledges the exit.
  const restartRequested = useRef(false);
  const openTerminal = useCallback(() => {
    const conn = connRef.current;
    if (!conn) return;
    const st = (conn as unknown as { ptyState?: string; pty?: string }).ptyState
      ?? (conn as unknown as { pty?: string }).pty;
    if (st === 'open') {
      restartRequested.current = true;
      try { conn.closePty(); } catch { restartRequested.current = false; }
      return;
    }
    if (st === 'opening' || st === 'closing') return;
    try {
      const h = termHandle.current;
      conn.openPty(h?.cols() ?? 80, h?.rows() ?? 24);
    } catch { /* ignore */ }
  }, []);
  const closeTerminal = useCallback(() => {
    restartRequested.current = false;
    try { connRef.current?.closePty(); } catch { /* ignore */ }
  }, []);
  const dirtyCount = snap.dirtyCount;
  useBeforeUnloadDirtyGuard(dirtyCount > 0);
  useRouteDirtyGuard(dirtyCount > 0);
  const connected = snap.phase === 'connected';
  // Explorer + open tabs are a cached snapshot of the remote filesystem, and
  // the shell shares that filesystem: `touch`, `mkdir`, `rm`, `mv`, `git
  // checkout`, build output, etc. never pass through the file protocol, so
  // nothing would invalidate the cache. Re-listing is idempotent and cheap
  // (one bounded Docker exec per directory), so sync visible state whenever
  // the terminal goes idle, on shell exit, periodically, and on focus — the
  // same moments a local editor re-reads the disk.
  const listInFlight = useRef(new Set<string>());
  const syncTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (syncTimer.current) clearTimeout(syncTimer.current); }, []);

  const refreshDir = useCallback(async (dir: string, cursor: number | null, append: boolean) => {
    const conn = connRef.current;
    if (!conn) return;
    // Serialize full (non-append) listings per directory: pagination legs
    // (append=true) belong to the listing that started them, while concurrent
    // manual + automatic refreshes of the same dir would otherwise interleave
    // pages from two different snapshots.
    if (!append) {
      if (listInFlight.current.has(dir)) return;
      listInFlight.current.add(dir);
    }
    try {
      dispatch({ type: 'dirLoading', dir, loading: true });
      const extra: Record<string, unknown> = {};
      if (cursor !== null && cursor !== undefined) extra.cursor = cursor;
      const value = await conn.request('list', dir, extra);
      const entries = (value.entries as { name: string; type: string }[]) ?? [];
      const next = (value.next_cursor as number | null) ?? null;
      dispatch({ type: 'children', dir, entries, nextCursor: next, append });
      if (next !== null && next !== undefined) await refreshDir(dir, next, true);
    } catch (e) { dispatch({ type: 'dirLoading', dir, loading: false, error: e instanceof Error ? e.message : 'Could not list directory' }); }
    finally { if (!append) listInFlight.current.delete(dir); }
  }, []);

  const openFile = useCallback(async (path: string) => {
    const conn = connRef.current;
    if (!conn || snap.files[path]) { dispatch({ type: 'activate', path }); const t = models.getText(path); if (t !== undefined) setEditorText(t); return; }
    try {
      const value = await conn.request('read', path);
      const text = String(value.content ?? '');
      models.ensure(path, text);
      models.setText(path, text);
      dispatch({ type: 'opened', path, version: String(value.version ?? ''), text });
      setEditorText(text);
    } catch (e) { dispatch({ type: 'notice', notice: notice('error', e instanceof Error ? `${baseName(path)}: ${e.message}` : 'Could not open file') }); }
  }, [models, snap.files]);

  // Reconcile open tabs with the server: files may have been created,
  // modified, or deleted by the terminal while the explorer cache and editor
  // models still hold the old snapshot. Clean tabs reload silently (like a
  // local editor); dirty tabs keep the user's edits and are flagged as
  // conflicted instead of being overwritten.
  const syncOpenFiles = useCallback(async () => {
    const conn = connRef.current;
    if (!conn || snapRef.current.phase !== 'connected') return;
    for (const path of Object.keys(snapRef.current.files)) {
      const file = snapRef.current.files[path];
      if (!file) continue;
      let value: Record<string, unknown>;
      try {
        value = await conn.request('stat', path);
      } catch (e) {
        if ((e as { code?: string })?.code !== 'NOT_FOUND') continue;
        const cur = snapRef.current.files[path];
        if (!cur) continue;
        const localText = models.getText(path) ?? cur.savedText;
        if (localText === cur.savedText) {
          models.remove(path);
          dispatch({ type: 'closedTab', path });
          dispatch({ type: 'notice', notice: notice('info', `${baseName(path)} was deleted outside the editor`) });
        } else {
          dispatch({ type: 'fileError', path, error: 'Deleted outside the editor' });
          dispatch({ type: 'notice', notice: notice('error', `${baseName(path)} was deleted outside the editor. Save to restore it, or close without saving.`) });
        }
        continue;
      }
      if (connRef.current !== conn || snapRef.current.phase !== 'connected') return;
      const serverVersion = typeof value.version === 'string' ? value.version : '';
      const cur = snapRef.current.files[path];
      if (!cur || !serverVersion || serverVersion === cur.version) continue;
      const localText = models.getText(path) ?? cur.savedText;
      if (localText === cur.savedText) {
        try {
          const latest = await conn.request('read', path);
          if (connRef.current !== conn || snapRef.current.phase !== 'connected') return;
          const text = String(latest.content ?? '');
          models.setText(path, text);
          dispatch({ type: 'externalUpdate', path, version: String(latest.version ?? serverVersion), text });
          if (snapRef.current.active === path) setEditorText(text);
        } catch { /* keep the stale copy; the next cycle retries */ }
      } else {
        dispatch({ type: 'conflict', path, serverText: cur.savedText, serverVersion });
        dispatch({ type: 'notice', notice: notice('info', `${baseName(path)} changed outside the editor. Your edits are preserved.`) });
      }
    }
  }, [models]);

  // Re-list the root plus every expanded directory (non-append, so deletions
  // vanish) and reconcile open tabs. Sequential to bound concurrent Docker
  // execs on the worker; per-dir in-flight guards make overlapping manual
  // and automatic syncs collapse instead of interleaving.
  const syncVisible = useCallback(async () => {
    const conn = connRef.current;
    if (!conn || snapRef.current.phase !== 'connected') return;
    const dirs = new Set<string>(['']);
    for (const [key, node] of Object.entries(snapRef.current.nodes)) {
      if (node.type === 'directory' && node.expanded) dirs.add(key);
    }
    for (const dir of dirs) {
      if (connRef.current !== conn || snapRef.current.phase !== 'connected') return;
      await refreshDir(dir, null, false);
    }
    await syncOpenFiles();
  }, [refreshDir, syncOpenFiles]);

  // Debounced sync after terminal output settles: a streaming command (build,
  // test run, `cat`) emits continuously, and only the pause afterwards means
  // the filesystem may have changed.
  const scheduleSync = useCallback((delayMs: number) => {
    if (syncTimer.current) clearTimeout(syncTimer.current);
    syncTimer.current = setTimeout(() => { syncTimer.current = null; void syncVisible(); }, delayMs);
  }, [syncVisible]);

  const doSave = useCallback(async (path: string): Promise<boolean> => {
    const conn = connRef.current;
    const file = snap.files[path];
    if (!conn || !file) return false;
    const currentText = models.getText(path) ?? file.savedText;
    if (currentText === file.savedText && !file.conflict) return true;
    dispatch({ type: 'saving', path, saving: true, error: null });
    try {
      const value = await conn.write(path, currentText, file.version);
      const sentText = models.getText(path) ?? currentText;
      if (sentText !== currentText) {
        dispatch({ type: 'saved', path, version: String(value.version ?? file.version), text: currentText });
        models.ensure(path, sentText);
        dispatch({ type: 'edited', texts: { ...models.textsRef.current } });
      } else {
        dispatch({ type: 'saved', path, version: String(value.version ?? file.version), text: currentText });
        dispatch({ type: 'edited', texts: { ...models.textsRef.current } });
      }
      dispatch({ type: 'notice', notice: notice('success', `Saved ${baseName(path)} to live container`) });
      return true;
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Save failed';
      const code = (e as { code?: string }).code;
      if (code === 'CONFLICT') {
        try {
          const latest = await conn.request('read', path);
          dispatch({ type: 'conflict', path, serverText: String(latest.content ?? ''), serverVersion: String(latest.version ?? '') });
          setPrompt({ kind: 'conflict', path });
        } catch { dispatch({ type: 'saving', path, saving: false, error: msg }); }
      } else dispatch({ type: 'saving', path, saving: false, error: msg });
      return false;
    }
  }, [models, snap.files]);

  const saveAll = useCallback(async () => {
    setSavingAll(true);
    let ok = 0, failed = 0;
    for (const path of Object.keys(snap.files)) {
      const file = snap.files[path];
      const currentText = models.getText(path) ?? file.savedText;
      if (currentText === file.savedText) continue;
      const done = await doSave(path);
      if (done) ok += 1; else failed += 1;
    }
    setSavingAll(false);
    if (failed > 0) dispatch({ type: 'notice', notice: notice('error', `Save All finished with ${failed} failure(s)`) });
    else if (ok > 0) dispatch({ type: 'notice', notice: notice('success', `Saved ${ok} file(s) to live container`) });
  }, [doSave, models, snap.files]);

  const reconnect = useCallback(() => { dispatch({ type: 'phase', phase: 'reconnecting', message: 'Reconnecting…' }); void connectRef.current?.(snap.attempt + 1); }, [snap.attempt]);
  const connectRef = useRef<(a: number) => Promise<void>>(async () => undefined);
  const linkRef = useRef<{ connectOnce(a: number): Promise<void>; autoReconnect(a: number): Promise<void>; disconnect(): void } | null>(null);

  const link = useWorkspaceConnection({
    workspaceId: id ?? '',
    onGrantMismatch: () => undefined,
    onConnected: (conn, runtimeId) => {
      void runtimeId;
      connRef.current = conn;
      const caps = conn.serverCapabilities;
      dispatch({ type: 'caps', caps: caps ? { textFileLimit: caps.textFileLimit } : null });
      dispatch({ type: 'readOnly', readOnly: false });
      dispatch({ type: 'phase', phase: 'connected', message: 'Connected', closeCode: null });
      dispatch({ type: 'notice', notice: notice('success', 'Connected to workspace') });
      void refreshDir('', null, false);
      for (const path of Object.keys(snapRef.current.files)) {
        const file = snapRef.current.files[path];
        const localText = models.getText(path) ?? file.savedText;
        const dirty = localText !== file.savedText;
        if (!dirty) {
          void conn.request('read', path).then((v) => { models.setText(path, String(v.content ?? '')); dispatch({ type: 'opened', path, version: String(v.version ?? ''), text: String(v.content ?? '') }); if (snapRef.current.active === path) setEditorText(String(v.content ?? '')); }).catch(() => undefined);
        } else {
          void conn.request('stat', path).then((v) => { if (String(v.version ?? '') !== file.version && String(v.version ?? '') !== '') dispatch({ type: 'conflict', path, serverText: file.savedText, serverVersion: String(v.version ?? file.version) }); }).catch(() => undefined);
        }
      }
      if (snapRef.current.ptyHadSession) {
        dispatch({ type: 'notice', notice: notice('info', 'Terminal process state was lost on reconnect; starting a fresh shell') });
        conn.openPty(80, 24);
      } else {
        conn.openPty(80, 24);
      }
    },
    onReconnecting: (attempt, delayMs) => {
      dispatch({ type: 'attempt', attempt });
      dispatch({ type: 'phase', phase: 'reconnecting', message: `Reconnecting… (attempt ${attempt}, ${(delayMs / 1000).toFixed(1)}s)`, closeCode: null });
    },
    onDisconnected: (message, code, retryable) => {
      connRef.current = null;
      dispatch({ type: 'pty', state: 'closed' });
      const next = snapRef.current.attempt + 1;
      if (retryable && next <= 5) {
        void linkRef.current?.autoReconnect(next);
      } else dispatch({ type: 'phase', phase: 'disconnected', message, closeCode: code });
    },
    onState: (s) => {
      const raw = s.state ?? s.code ?? s.reason ?? '';
      const v = String(raw);
      const lower = v.toLowerCase();
      if (lower.includes('read_only') || lower === 'read-only' || lower.includes('read only')) dispatch({ type: 'readOnly', readOnly: true });
      else if (lower.includes('read_write') || lower.includes('writable')) dispatch({ type: 'readOnly', readOnly: false });
      if (lower.includes('drain') || lower.includes('stopp') || lower.includes('unavailable')) dispatch({ type: 'notice', notice: notice('info', `Workspace state: ${v}`) });
    },
    onPtyOutput: (d) => { termLines.current?.write(d); scheduleSync(1200); },
    onPtyExit: (info) => {
      const exit = connRef.current?.ptyExit ?? info ?? { code: 0, reason: 'exited' };
      dispatch({ type: 'pty', state: 'exited', exit });
      // Never leave a blank terminal: print why the shell went away and how
      // to get a new one. Without this a failed launch looks identical to a
      // shell that silently drops command output.
      try { termHandle.current?.write(`\r\n[terminal ${exit.code === 0 ? 'closed' : 'exited'} (code ${exit.code}, ${exit.reason}) — press + for a new shell]\r\n`); } catch { /* ignore */ }
      // A finished command is the most likely moment the filesystem changed
      // (rm/mv/build/git); sync immediately instead of waiting for idle.
      void syncVisible();
      if (restartRequested.current) {
        restartRequested.current = false;
        try {
          const h = termHandle.current;
          connRef.current?.openPty(h?.cols() ?? 80, h?.rows() ?? 24);
        } catch { /* ignore */ }
      }
    },
    onPtyState: (s) => {
      dispatch({ type: 'pty', state: s });
      if (s === 'open' || s === 'opening') dispatch({ type: 'ptyActive', active: true });
      // Fit-time resizes during 'opening' are dropped by the transport guard;
      // re-sync the measured size once the shell is actually open so `ls`
      // output wraps to the real terminal width.
      if (s === 'open') {
        try {
          const h = termHandle.current;
          if (h && h.cols() > 0) connRef.current?.resize(h.cols(), h.rows());
        } catch { /* best effort */ }
      }
    },
  });
  const snapRef = useRef(snap);
  snapRef.current = snap;
  useEffect(() => { linkRef.current = link; connectRef.current = (a: number) => { dispatch({ type: 'attempt', attempt: a }); return link.connectOnce(a); }; }, [link]);

  useEffect(() => { models.onChange(() => { const t = { ...models.textsRef.current }; dispatch({ type: 'edited', texts: t }); const active = snapRef.current.active; if (active) { const v = t[active]; if (v !== undefined) setEditorText(v); } }); }, [models]);
  useEffect(() => { dispatch({ type: 'phase', phase: 'loading', message: 'Connecting…' }); void connectRef.current?.(0); return () => { link.disconnect(); }; }, []); // eslint-disable-line react-hooks/exhaustive-deps
  // Catch changes from anywhere else (background jobs, another client) and
  // refresh when the user comes back, like a local editor does on focus.
  useEffect(() => {
    if (!connected) return;
    const timer = setInterval(() => { void syncVisible(); }, 15000);
    return () => clearInterval(timer);
  }, [connected, syncVisible]);
  useEffect(() => {
    const onFocus = () => { if (snapRef.current.phase === 'connected') void syncVisible(); };
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [syncVisible]);

  const closeTab = useCallback((path: string) => {
    const file = snapRef.current.files[path];
    const localText = models.getText(path) ?? file?.savedText ?? '';
    if (file && localText !== file.savedText) { setPrompt({ kind: 'closeDirty', path }); return; }
    models.remove(path);
    dispatch({ type: 'closedTab', path });
    const rest = snapRef.current.tabs.filter((t) => t !== path);
    const next = rest[rest.length - 1] ?? null;
    if (next) { dispatch({ type: 'activate', path: next }); setEditorText(models.getText(next) ?? ''); }
  }, [models]);

  const activate = useCallback((path: string) => { dispatch({ type: 'activate', path }); const t = models.getText(path); setEditorText(t ?? snapRef.current.files[path]?.savedText ?? ''); }, [models]);

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === 's' && !e.shiftKey) { e.preventDefault(); const a = snapRef.current.active; if (a) void doSave(a); }
      else if (mod && e.key.toLowerCase() === 's' && e.shiftKey) { e.preventDefault(); void saveAll(); }
      else if (mod && e.key.toLowerCase() === 'w') { e.preventDefault(); const a = snapRef.current.active; if (a) closeTab(a); }
      else if (mod && e.key.toLowerCase() === 'b') { e.preventDefault(); panels.toggleExplorer(); }
      else if (e.ctrlKey && e.key === '`') { e.preventDefault(); panels.toggleTerminal(); }
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [doSave, saveAll, closeTab, panels]);

  const doMkdir = useCallback(async (dir: string, name: string) => {
    const conn = connRef.current; if (!conn) return;
    const target = joinPath(dir === '' ? '' : (snap.nodes[dir]?.type === 'file' ? parentPath(dir) : dir), name);
    try { await conn.request('mkdir', target); dispatch({ type: 'notice', notice: notice('success', `Created ${target}`) }); await refreshDir(parentPath(target), null, false); }
    catch (e) { dispatch({ type: 'notice', notice: notice('error', e instanceof Error ? e.message : 'Could not create folder') }); }
  }, [refreshDir, snap.nodes]);
  const doCreateFile = useCallback(async (dir: string, name: string) => {
    const conn = connRef.current; if (!conn) return;
    const target = joinPath(dir === '' ? '' : (snap.nodes[dir]?.type === 'file' ? parentPath(dir) : dir), name);
    try { await conn.request('create_file', target); dispatch({ type: 'notice', notice: notice('success', `Created ${target}`) }); await refreshDir(parentPath(target), null, false); await openFile(target); }
    catch (e) { dispatch({ type: 'notice', notice: notice('error', e instanceof Error ? e.message : 'Could not create file') }); }
  }, [openFile, refreshDir, snap.nodes]);

  const confirmPrompt = useCallback(async () => {
    if (!prompt) return;
    const conn = connRef.current;
    if (prompt.kind === 'createFile' && promptValue.trim()) { const dir = prompt.dir ?? ''; const name = promptValue.trim(); if (!isValidName(name)) { dispatch({ type: 'notice', notice: notice('error', 'Invalid file name') }); return; } setPrompt(null); setPromptValue(''); await doCreateFile(dir, name); return; }
    if (prompt.kind === 'createFolder' && promptValue.trim()) { const dir = prompt.dir ?? ''; const name = promptValue.trim(); if (!isValidName(name)) { dispatch({ type: 'notice', notice: notice('error', 'Invalid folder name') }); return; } setPrompt(null); setPromptValue(''); await doMkdir(dir, name); return; }
    if (prompt.kind === 'rename' && prompt.path && promptValue.trim()) {
      const oldPath = prompt.path; const name = promptValue.trim();
      if (!isValidName(name)) { dispatch({ type: 'notice', notice: notice('error', 'Invalid name') }); return; }
      const newPath = joinPath(parentPath(oldPath), name);
      setPrompt(null); setPromptValue('');
      if (!conn) return;
      try {
        const file = snapRef.current.files[oldPath];
        const extra: Record<string, unknown> = { target: newPath };
        if (file) extra.expected_version = file.version;
        else {
          try { const st = await conn.request('stat', oldPath); if (typeof st.version === 'string' && st.version) extra.expected_version = st.version; } catch { /* server decides */ }
        }
        await conn.request('rename', oldPath, extra);
        models.rename(oldPath, newPath);
        dispatch({ type: 'renamed', oldPath, newPath });
        await refreshDir(parentPath(oldPath), null, false);
        if (parentPath(oldPath) !== parentPath(newPath)) await refreshDir(parentPath(newPath), null, false);
      } catch (e) { dispatch({ type: 'notice', notice: notice('error', e instanceof Error ? e.message : 'Rename failed') }); }
      return;
    }
    if (prompt.kind === 'delete' && prompt.path) {
      const target = prompt.path; setPrompt(null); setPromptValue('');
      if (!conn) return;
      try {
        const file = snapRef.current.files[target];
        const extra: Record<string, unknown> = {};
        if (file) extra.expected_version = file.version;
        else {
          try { const st = await conn.request('stat', target); if (typeof st.version === 'string' && st.version) extra.expected_version = st.version; } catch { /* server decides */ }
        }
        await conn.request('delete', target, extra);
        models.remove(target);
        dispatch({ type: 'removed', path: target });
        await refreshDir(parentPath(target), null, false);
      } catch (e) { dispatch({ type: 'notice', notice: notice('error', e instanceof Error ? `${target}: ${e instanceof Error ? e.message : 'Delete failed'}. Note: only empty directories can be removed.` : 'Delete failed') }); }
      return;
    }
    if (prompt.kind === 'stop') {
      const rt = snapRef.current.runtime;
      setPrompt(null); setPromptValue('');
      if (!rt) return;
      try { await interactive.stop(rt.id); dispatch({ type: 'notice', notice: notice('info', 'Stop requested. Live container changes may disappear unless a durable revision has completed.') }); }
      catch (e) { dispatch({ type: 'notice', notice: notice('error', e instanceof Error ? e.message : 'Stop failed') }); }
      return;
    }
    if (prompt.kind === 'closeDirty' && prompt.path) {
      const target = prompt.path; setPrompt(null);
      models.remove(target); dispatch({ type: 'closedTab', path: target });
      return;
    }
    setPrompt(null); setPromptValue('');
  }, [prompt, promptValue, doCreateFile, doMkdir, models, snap.nodes, refreshDir]);

  const stopRuntime = useCallback(async () => {
    const rt = snapRef.current.runtime;
    if (!rt) return;
    setPrompt({ kind: 'stop' });
  }, []);

  const dirtyFor = useCallback((p: string) => { const f = snap.files[p]; if (!f) return false; return (models.getText(p) ?? f.savedText) !== f.savedText; }, [models, snap.files]);
  const header = useMemo(() => ({ name: snap.workspace?.name ?? 'Workspace', runtimeState: snap.runtime?.state ?? snap.phase }), [snap.workspace, snap.runtime, snap.phase]);

  return (
    <div className="ide-shell" data-testid="workspace-ide">
      <IDETitleBar workspaceId={id ?? ''} name={header.name} runtimeState={header.runtimeState} liveOnly phase={snap.phase} dirtyCount={dirtyCount} saving={savingAll} onSaveAll={() => void saveAll()} onReconnect={reconnect} onStop={() => void stopRuntime()} />
      {(snap.phase === 'disconnected' || snap.phase === 'reconnecting' || snap.phase === 'fatal') && (
        <div className="ide-banner" role="alert">
          <span>{snap.message}{snap.closeCode !== null ? ` (code ${snap.closeCode})` : ''} · Unsaved work is kept in memory.</span>
          <button type="button" className="ide-btn ide-btn-primary" onClick={reconnect}>Reconnect</button>
        </div>
      )}
      <div className="ide-body">
        <ActivityBar explorerOpen={panels.explorerOpen} onToggleExplorer={panels.toggleExplorer} onToggleTerminal={panels.toggleTerminal} terminalCollapsed={panels.terminalCollapsed} />
        {panels.explorerOpen && (
          <aside className="ide-side" style={{ width: panels.explorerWidth }}>
            <ExplorerPanel snap={snap} wsName={header.name} connected={connected} ops={{
              onToggleDir: (p) => { const n = snap.nodes[p]; if (!n) return; if (!n.expanded) void refreshDir(p, null, false); dispatch({ type: 'toggle', dir: p }); },
              onOpenFile: (p) => void openFile(p),
              onRefreshDir: (p) => void refreshDir(snap.nodes[p]?.type === 'file' ? parentPath(p) : p, null, false),
              onRefreshRoot: () => void refreshDir('', null, false),
              onCreateFile: (d) => { setPromptValue(''); setPrompt({ kind: 'createFile', dir: d }); },
              onCreateFolder: (d) => { setPromptValue(''); setPrompt({ kind: 'createFolder', dir: d }); },
              onRename: (p) => { setPromptValue(baseName(p)); setPrompt({ kind: 'rename', path: p }); },
              onDelete: (p) => setPrompt({ kind: 'delete', path: p }),
              onCopyPath: (p) => { const done = () => dispatch({ type: 'notice', notice: notice('success', 'Copied relative path') }); try { const nav = navigator as Navigator & { clipboard?: { writeText(t: string): Promise<void> } }; if (nav.clipboard) nav.clipboard.writeText(p).then(done).catch(() => dispatch({ type: 'notice', notice: notice('info', `Path: ${p}`) })); else dispatch({ type: 'notice', notice: notice('info', `Path: ${p}`) }); } catch { dispatch({ type: 'notice', notice: notice('info', `Path: ${p}`) }); } },
            }} />
            <div className="ide-resizer" role="separator" aria-orientation="vertical" aria-label="Resize explorer" tabIndex={0} onKeyDown={(e) => { if (e.key === 'ArrowLeft') panels.setExplorerWidth(Math.max(180, panels.explorerWidth - 16)); if (e.key === 'ArrowRight') panels.setExplorerWidth(Math.min(520, panels.explorerWidth + 16)); }} onMouseDown={(e) => { const start = e.clientX; const base = panels.explorerWidth; const move = (ev: MouseEvent) => panels.setExplorerWidth(Math.min(520, Math.max(180, base + ev.clientX - start))); const up = () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); }; window.addEventListener('mousemove', move); window.addEventListener('mouseup', up); }} />
          </aside>
        )}
        <main className="ide-main">
          <EditorTabs tabs={snap.tabs} active={snap.active} dirty={dirtyFor} onActivate={activate} onClose={closeTab} onCloseOthers={(keep) => { for (const t of [...snapRef.current.tabs]) { if (t !== keep) { const f = snapRef.current.files[t]; const txt = models.getText(t) ?? f?.savedText ?? ''; if (!f || txt === f.savedText) { models.remove(t); dispatch({ type: 'closedTab', path: t }); } } } }} />
          {snap.phase === 'loading' || snap.phase === 'connecting' ? <p role="status" className="ide-status-line">Connecting…</p> : null}
          <div className="ide-editor-wrap">
            <EditorPane
              active={snap.active}
              text={snap.active ? editorText : ''}
              readOnly={snap.readOnly}
              fontSize={13}
              wordWrap={panels.wordWrap}
              minimap={panels.minimap}
              runtimeId={snap.runtime?.id ?? 'workspace'}
              model={snap.active ? models.get(snap.active) ?? null : null}
              onChangeText={(v, line, col) => { const a = snapRef.current.active; if (!a) return; models.setText(a, v); setEditorText(v); setLineCol(`Ln ${line}, Col ${col}`); dispatch({ type: 'edited', texts: { ...models.textsRef.current } }); }}
              onToggleWrap={() => panels.setWordWrap(!panels.wordWrap)}
              onToggleMinimap={() => panels.setMinimap(!panels.minimap)}
            />
            {snap.active && snap.files[snap.active]?.error && <p role="alert" className="ide-inline-error">{snap.files[snap.active]?.error}</p>}
          </div>
          {!panels.terminalCollapsed && (
            <TerminalPanel
              pty={snap.pty}
              exit={snap.ptyExit}
              collapsed={false}
              maximized={maxTerm}
              height={maxTerm ? undefined : panels.terminalHeight}
              onOpen={openTerminal}
              onClosePty={closeTerminal}
              onClear={() => termHandle.current?.clear()}
              onToggleCollapse={panels.toggleTerminal}
              onToggleMax={() => setMaxTerm((v) => !v)}
              onInput={handleTermInput}
              onResize={handleTermResize}
              onHeightChange={handleTermHeight}
              register={registerTerminal}
            />
          )}
        </main>
      </div>
      <StatusBar phase={snap.phase} pty={snap.pty} dirtyCount={dirtyCount} active={snap.active} lineCol={lineCol} readOnly={snap.readOnly} connected={connected} />
      <ToastRegion notices={snap.notices} onDismiss={(nid) => dispatch({ type: 'dismissNotice', id: nid })} />
      {prompt?.kind === 'createFile' && <Dialog title="New file" onClose={() => setPrompt(null)} onConfirm={() => void confirmPrompt()} confirmLabel="Create"><label>File name<input autoFocus value={promptValue} onChange={(e) => setPromptValue(e.target.value)} placeholder="example.py" /></label></Dialog>}
      {prompt?.kind === 'createFolder' && <Dialog title="New folder" onClose={() => setPrompt(null)} onConfirm={() => void confirmPrompt()} confirmLabel="Create"><label>Folder name<input autoFocus value={promptValue} onChange={(e) => setPromptValue(e.target.value)} placeholder="src" /></label></Dialog>}
      {prompt?.kind === 'rename' && <Dialog title={`Rename ${prompt.path}`} onClose={() => setPrompt(null)} onConfirm={() => void confirmPrompt()} confirmLabel="Rename"><label>New name<input autoFocus value={promptValue} onChange={(e) => setPromptValue(e.target.value)} /></label></Dialog>}
      {prompt?.kind === 'delete' && <Dialog title="Delete" onClose={() => setPrompt(null)} onConfirm={() => void confirmPrompt()} confirmLabel="Delete" danger><p>Delete <code>{prompt.path}</code>? Only empty directories can be removed by the backend.</p></Dialog>}
      {prompt?.kind === 'stop' && <Dialog title="Stop runtime" onClose={() => setPrompt(null)} onConfirm={() => void confirmPrompt()} confirmLabel="Stop runtime" danger><p>Stop this runtime? Live container changes may disappear unless a durable revision has completed.{dirtyCount > 0 ? ` There are ${dirtyCount} unsaved file(s) in the editor.` : ''}</p></Dialog>}
      {prompt?.kind === 'closeDirty' && <Dialog title="Unsaved changes" onClose={() => setPrompt(null)} onConfirm={() => { if (prompt.path) { void doSave(prompt.path).then((ok) => { if (ok && prompt.path) { models.remove(prompt.path); dispatch({ type: 'closedTab', path: prompt.path }); setPrompt(null); } }); } }} confirmLabel="Save and close"><p><code>{prompt.path}</code> has unsaved changes.</p><p><button type="button" className="ide-btn" onClick={() => { if (prompt.path) { models.remove(prompt.path); dispatch({ type: 'closedTab', path: prompt.path }); } setPrompt(null); }}>Don&apos;t save</button></p></Dialog>}
      {prompt?.kind === 'conflict' && prompt.path && <Dialog title="Conflict: file changed on server" onClose={() => setPrompt(null)} confirmLabel="Keep editing locally" onConfirm={() => setPrompt(null)}><p><code>{prompt.path}</code> changed outside the editor. Your edits are preserved.</p><p><button type="button" className="ide-btn" onClick={() => { const p = prompt.path as string; const c = snapRef.current.files[p]?.conflict; if (c) { models.setText(p, c.serverText); dispatch({ type: 'resolveConflict', path: p, keep: 'server', serverText: c.serverText, serverVersion: c.serverVersion }); setEditorText(c.serverText); } setPrompt(null); }}>Reload server version (discards my edits)</button></p><p><button type="button" className="ide-btn" onClick={() => { const p = prompt.path as string; const c = snapRef.current.files[p]?.conflict; if (c) { try { void navigator.clipboard?.writeText(models.getText(p) ?? ''); } catch { /* ignore */ } dispatch({ type: 'notice', notice: notice('info', 'Local content copied to clipboard') }); } }}>Copy local content</button></p></Dialog>}
    </div>
  );
}
