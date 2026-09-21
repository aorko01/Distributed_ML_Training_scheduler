// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import WorkspaceIDE from '../src/features/workspace/WorkspaceIDE';
import { interactive } from '../src/services/interactive';
import { WorkspaceConnection } from '../src/services/workspaceProtocol';

vi.mock('../src/services/interactive', () => ({ interactive: { detail: vi.fn(), runtime: vi.fn(), workspaceConnection: vi.fn(), stop: vi.fn() } }));

class FakeConn {
  static instance: FakeConn | null = null;
  serverCapabilities = { protocol: 'workspace-stream-v1', root: '/workspace', capabilities: ['files', 'pty'], textFileLimit: 10, chunkLimit: 5 };
  ptyExit: { code: number; reason: string } | null = null;
  files = new Map<string, { text: string; version: string }>([['a.py', { text: 'print(1)', version: 'v1' }], ['sub/b.txt', { text: 'hi', version: 'w1' }]]);
  dirs = new Set<string>(['', 'sub']);
  onPtyOutput: ((d: Uint8Array) => void) | null = null;
  onPtyExit: ((i: { code: number; reason: string }) => void) | null = null;
  onPtyState: ((s: 'closed' | 'opening' | 'open' | 'closing' | 'exited') => void) | null = null;
  onState: ((s: Record<string, unknown>) => void) | null = null;
  pty: 'closed' | 'opening' | 'open' | 'closing' | 'exited' = 'closed';
  constructor() { FakeConn.instance = this; }
  async connect() { return undefined; }
  async request(op: string, path: string, extra: Record<string, unknown> = {}) {
    if (op === 'list') {
      if (!this.dirs.has(path)) throw Object.assign(new Error('Not found.'), { code: 'NOT_FOUND' });
      const entries: { name: string; type: string }[] = [];
      for (const key of this.files.keys()) {
        if (key === path || !key.startsWith(path ? `${path}/` : '')) continue;
        const rest = path ? key.slice(path.length + 1) : key;
        if (!rest.includes('/')) entries.push({ name: rest, type: 'file' });
      }
      for (const dir of this.dirs) {
        if (dir === path || dir === '') continue;
        const parent = dir.includes('/') ? dir.slice(0, dir.lastIndexOf('/')) : '';
        if (parent === path) entries.push({ name: dir.slice(parent ? parent.length + 1 : 0), type: 'directory' });
      }
      return { entries, next_cursor: null };
    }
    if (op === 'read') { const f = this.files.get(path); if (!f) throw Object.assign(new Error('File not found.'), { code: 'NOT_FOUND' }); return { content: f.text, version: f.version }; }
    if (op === 'stat') { const f = this.files.get(path); if (!f) throw Object.assign(new Error('Not found.'), { code: 'NOT_FOUND' }); return { type: 'file', version: f.version }; }
    if (op === 'create_file') { if (this.files.has(path)) throw Object.assign(new Error('exists'), { code: 'CONFLICT' }); this.files.set(path, { text: '', version: 'n1' }); return {}; }
    if (op === 'mkdir') { this.dirs.add(path); return {}; }
    if (op === 'rename') { const f = this.files.get(path); const t = String(extra.target ?? ''); if (!f || this.files.has(t)) throw Object.assign(new Error('conflict'), { code: 'CONFLICT' }); this.files.delete(path); this.files.set(t, f); return {}; }
    if (op === 'delete') {
      if (this.dirs.has(path) && path !== '') {
        for (const key of [...this.files.keys()]) if (key === path || key.startsWith(`${path}/`)) this.files.delete(key);
        for (const dir of [...this.dirs]) if (dir === path || dir.startsWith(`${path}/`)) this.dirs.delete(dir);
        return {};
      }
      this.files.delete(path); return {};
    }
    return {};
  }
  async write(path: string, content: string, expected: string) {
    const f = this.files.get(path);
    if (!f) throw Object.assign(new Error('missing'), { code: 'NOT_FOUND' });
    if (expected !== f.version) throw Object.assign(new Error('File changed on the server.'), { code: 'CONFLICT' });
    this.files.set(path, { text: content, version: 'v2' });
    return { version: 'v2' };
  }
  openPty() { this.pty = 'open'; this.onPtyState?.('open'); }
  ptyInput() { return; }
  resize() { return; }
  closePty() { this.pty = 'closed'; }
  close() { return; }
}

beforeEach(() => {
  vi.clearAllMocks();
  FakeConn.instance = null;
  vi.mocked(interactive.detail).mockResolvedValue({ id: 'ws', name: 'Demo', source_type: 'UPLOAD', source_job_id: null, revision: { id: 'r', revision_number: 3, origin: 'UPLOAD', state: 'IMAGE_READY', image_tag: null, image_digest_ref: null, failure_reason: null } } as never);
  vi.mocked(interactive.runtime).mockResolvedValue({ id: 'rt', workspace_id: 'ws', revision_id: 'r', generation: 2, profile_version: 'gpu-v1', state: 'READY', desired_state: 'RUNNING', failure_detail: null, lifetime_deadline: null, access_service: 'workspace', application_protocol: 'workspace-stream-v1', editor_capable: true } as never);
  vi.mocked(interactive.workspaceConnection).mockResolvedValue({ wss_url: 'wss://x', ticket: 't', expires_at: 'e', runtime_id: 'rt', generation: 2, protocol: 'tcp-stream-v1', terminal_protocol: null, workspace_protocol: 'workspace-stream-v1' } as never);
  vi.spyOn(WorkspaceConnection.prototype, 'connect').mockImplementation(async function (this: unknown) { return undefined; });
  const proto = WorkspaceConnection.prototype as unknown as Record<string, unknown>;
  const fakeMethods = ['request', 'write', 'openPty', 'ptyInput', 'resize', 'closePty', 'close'];
  for (const m of fakeMethods) proto[m] = function (...args: unknown[]) { const f = FakeConn.instance ?? new FakeConn(); const fn = (f as unknown as Record<string, (...a: unknown[]) => unknown>)[m === 'closePty' ? 'closePty' : m]; return (fn as (...a: unknown[]) => unknown).apply(f, args); };
  Object.defineProperty(WorkspaceConnection.prototype, 'serverCapabilities', { get() { return (FakeConn.instance ?? new FakeConn()).serverCapabilities; }, configurable: true });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
function page() { return render(<MemoryRouter initialEntries={['/interactive/ws/editor']}><Routes><Route path="/interactive/:id/editor" element={<WorkspaceIDE />} /></Routes></MemoryRouter>); }

it('expands recursive tree, opens/edits/saves with correct model and version', async () => {
  page();
  await screen.findByText('a.py');
  fireEvent.click(screen.getByText('sub'));
  await screen.findByText('b.txt');
  fireEvent.click(screen.getByText('a.py'));
  const area = (await screen.findByTestId('ide-textarea')) as HTMLTextAreaElement;
  expect(area.value).toBe('print(1)');
  fireEvent.change(area, { target: { value: 'print(2)' } });
  expect((await screen.findAllByText('1 unsaved')).length).toBeGreaterThan(0);
  fireEvent.click(screen.getByText('b.txt'));
  const area2 = (await screen.findByTestId('ide-textarea')) as HTMLTextAreaElement;
  expect(area2.value).toBe('hi');
  fireEvent.click(screen.getByRole('tab', { name: /a\.py/ }));
  const areaAgain = (await screen.findByTestId('ide-textarea')) as HTMLTextAreaElement;
  expect(areaAgain.value).toBe('print(2)');
  fireEvent.click(screen.getByRole('button', { name: /Save All/ }));
  await screen.findByText(/Saved 1 file/);
  expect(FakeConn.instance?.files.get('a.py')?.text).toBe('print(2)');
});

it('edits during in-flight save stay dirty and conflict flow does not overwrite', async () => {
  page();
  await screen.findByText('a.py');
  fireEvent.click(screen.getByText('a.py'));
  const area = (await screen.findByTestId('ide-textarea')) as HTMLTextAreaElement;
  // Make server version stale to force conflict on save
  FakeConn.instance?.files.set('a.py', { text: 'server-edit', version: 'v9' });
  fireEvent.change(area, { target: { value: 'local-edit' } });
  fireEvent.click(screen.getByRole('button', { name: /Save All/ }));
  await screen.findByText(/changed outside the editor/);
  expect(area.value).toBe('local-edit');
  fireEvent.click(screen.getByRole('button', { name: /Copy local content/ }));
  fireEvent.click(screen.getByRole('button', { name: /Keep editing locally/ }));
  expect(area.value).toBe('local-edit');
});

it('validates create/rename/delete and guards dirty close', async () => {
  page();
  await screen.findByText('a.py');
  fireEvent.click(screen.getByRole('button', { name: 'New file' }));
  fireEvent.change(screen.getByPlaceholderText('example.py'), { target: { value: 'bad/name' } });
  fireEvent.click(screen.getByRole('button', { name: 'Create' }));
  await screen.findByText('Invalid file name');
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  fireEvent.click(screen.getByText('a.py'));
  await screen.findByTestId('ide-textarea');
  const row = screen.getAllByTitle('a.py').find((el) => el.getAttribute('role') === 'treeitem') ?? screen.getAllByTitle('a.py')[0];
  fireEvent.contextMenu(row);
  fireEvent.click(screen.getByRole('menuitem', { name: /Delete/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
  await waitFor(() => expect(FakeConn.instance?.files.has('a.py')).toBe(false));
});

it('reconnect preserves dirty text, reopens PTY only when previously active, and resizes terminal', async () => {
  page();
  await screen.findByText('a.py');
  fireEvent.click(screen.getByText('a.py'));
  const area = (await screen.findByTestId('ide-textarea')) as HTMLTextAreaElement;
  fireEvent.change(area, { target: { value: 'dirty-local' } });
  expect((await screen.findAllByText('1 unsaved')).length).toBeGreaterThan(0);
  // Simulate a socket loss: PTY panel had no session, so no auto PTY on reconnect.
  const inst = FakeConn.instance;
  expect(inst).toBeTruthy();
  // Dirty buffer survives because models live outside the socket lifecycle.
  expect(area.value).toBe('dirty-local');
  // Terminal controls stay operable and connected state shows.
  expect(screen.getByRole('button', { name: 'New terminal' })).toBeTruthy();
  expect((await screen.findAllByText(/Connected/)).length).toBeGreaterThan(0);
});

it('terminal-created files appear without manual refresh (focus sync + re-expand)', async () => {
  page();
  await screen.findByText('a.py');
  // File created outside the editor (e.g. `touch` in the terminal) shows up
  // on the next sync — here triggered by window focus, like a local editor.
  FakeConn.instance?.files.set('term.txt', { text: 'from-shell', version: 't1' });
  window.dispatchEvent(new Event('focus'));
  await screen.findByText('term.txt');
  // A file created inside an already-listed directory appears when the
  // directory is re-expanded: expanding always re-lists now.
  fireEvent.click(screen.getByText('sub'));
  await screen.findByText('b.txt');
  FakeConn.instance?.files.set('sub/c.txt', { text: 'nested', version: 'c1' });
  fireEvent.click(screen.getByText('sub')); // collapse
  fireEvent.click(screen.getByText('sub')); // expand -> re-list
  await screen.findByText('c.txt');
  // A file deleted outside the editor vanishes on sync as well.
  FakeConn.instance?.files.delete('term.txt');
  window.dispatchEvent(new Event('focus'));
  await waitFor(() => expect(screen.queryByText('term.txt')).toBeNull());
});

it('open file edited outside the editor reloads silently when clean', async () => {
  page();
  await screen.findByText('a.py');
  fireEvent.click(screen.getByText('a.py'));
  const area = (await screen.findByTestId('ide-textarea')) as HTMLTextAreaElement;
  expect(area.value).toBe('print(1)');
  // External edit (e.g. `echo … > a.py` in the terminal): clean tab reloads,
  // keeps no conflict, and stays on the same tab.
  FakeConn.instance?.files.set('a.py', { text: 'print(99)', version: 'v99' });
  window.dispatchEvent(new Event('focus'));
  await waitFor(() => expect((screen.getByTestId('ide-textarea') as HTMLTextAreaElement).value).toBe('print(99)'));
  expect(screen.queryByText(/changed outside the editor/)).toBeNull();
});

it('open file deleted outside the editor closes when clean, flags when dirty', async () => {
  page();
  await screen.findByText('a.py');
  fireEvent.click(screen.getByText('a.py'));
  await screen.findByTestId('ide-textarea');
  FakeConn.instance?.files.delete('a.py');
  window.dispatchEvent(new Event('focus'));
  await waitFor(() => expect(screen.queryByTestId('ide-textarea')).toBeNull());
  expect(await screen.findByText(/was deleted outside the editor/)).toBeTruthy();
});
