import { afterEach, expect, it, vi } from 'vitest';
import { WorkspaceConnection, WorkspaceType, record, metadataBytes, packChunk, sha256HexSync } from '../src/services/workspaceProtocol';
import type { ConnectionGrant } from '../src/services/interactive';

const grant: ConnectionGrant = { wss_url: 'wss://gateway.example/v1/connect/runtime/workspace', ticket: 'private', expires_at: 'later', runtime_id: 'runtime', generation: 1, protocol: 'tcp-stream-v1', terminal_protocol: null, workspace_protocol: 'workspace-stream-v1' };

class FakeSocket {
  static OPEN = 1;
  static instances: FakeSocket[] = [];
  readyState = FakeSocket.OPEN;
  binaryType = '';
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  sent: unknown[] = [];
  constructor(url: string) { this.url = url; FakeSocket.instances.push(this); }
  send(value: unknown) { this.sent.push(value); }
  close() { this.readyState = 3; }
}

afterEach(() => { vi.unstubAllGlobals(); FakeSocket.instances = []; vi.useRealTimers(); });

function setup() { vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket); }
async function connected() {
  setup();
  const connection = new WorkspaceConnection(grant);
  const done = connection.connect();
  const socket = FakeSocket.instances[FakeSocket.instances.length - 1] as FakeSocket;
  socket.onopen?.();
  socket.onmessage?.({ data: JSON.stringify({ type: 'ready', protocol: 'tcp-stream-v1' }) });
  socket.onmessage?.({ data: record(WorkspaceType.READY, metadataBytes({ protocol: 'workspace-stream-v1', root: '/workspace', capabilities: ['files', 'pty'], text_file_limit: 2097152, chunk_limit: 32768 })).slice(0) });
  await done;
  return { connection, socket };
}
function msg(bytes: ArrayBuffer | Uint8Array): ArrayBuffer { return bytes instanceof Uint8Array ? bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer : bytes; }
const tick = (n = 5) => new Promise((r) => setTimeout(r, n));

it('reports the gateway close code on early disconnect', async () => {
  setup();
  const connection = new WorkspaceConnection(grant);
  const check = connection.connect();
  const rejection = expect(check).rejects.toThrow('Workspace request rejected by policy');
  const socket = FakeSocket.instances[0] as FakeSocket;
  socket.onclose?.({ code: 4403 });
  await rejection;
  expect(connection.lastCloseCode).toBe(4403);
});

it('rejects an invalid grant protocol', async () => {
  setup();
  const bad = new WorkspaceConnection({ ...grant, workspace_protocol: null });
  await expect(bad.connect()).rejects.toThrow('Workspace unavailable');
});

it('completes gateway ready, READY, list, PTY_OPENED, streamed read, PTY output and exit without closing', async () => {
  const { connection, socket } = await connected();
  expect(connection.serverCapabilities?.protocol).toBe('workspace-stream-v1');
  const states: string[] = [];
  connection.onPtyState = (s) => { states.push(s); };
  const outputs: Uint8Array[] = [];
  connection.onPtyOutput = (d) => { outputs.push(d); };
  let exitInfo: { code: number; reason: string } | null = null;
  connection.onPtyExit = (info) => { exitInfo = info; };
  // root list via split frames (split inside header)
  const listPromise = connection.request('list', '');
  const listId = (socket.sent[socket.sent.length - 1] as ArrayBuffer);
  const listRaw = JSON.parse(new TextDecoder().decode(new Uint8Array(listId).slice(6))).id as string;
  const listResult = new Uint8Array(record(WorkspaceType.RESULT, metadataBytes({ id: listRaw, state: 'ok', entries: [{ name: 'a.py', type: 'file' }], next_cursor: null })) as ArrayBuffer);
  socket.onmessage?.({ data: msg(listResult.slice(0, 4)) });
  socket.onmessage?.({ data: msg(listResult.slice(4)) });
  const listValue = await listPromise;
  expect((listValue.entries as unknown[]).length).toBe(1);
  // PTY open handshake
  connection.openPty(80, 24);
  expect(connection.ptyState).toBe('opening');
  connection.openPty(80, 24); // idempotent while opening
  expect(socket.sent.filter((s) => new Uint8Array(s as ArrayBuffer)[1] === WorkspaceType.PTY_OPEN).length).toBe(1);
  connection.ptyInput('early'); // must not send stdin before PTY_OPENED
  expect(socket.sent.filter((s) => new Uint8Array(s as ArrayBuffer)[1] === WorkspaceType.PTY_STDIN).length).toBe(0);
  socket.onmessage?.({ data: record(WorkspaceType.PTY_OPENED, metadataBytes({ protocol: 'workspace-stream-v1' })) });
  expect(connection.ptyState).toBe('open');
  connection.ptyInput('ls\n');
  connection.resize(100, 30);
  expect(socket.sent.filter((s) => new Uint8Array(s as ArrayBuffer)[1] === WorkspaceType.PTY_STDIN).length).toBe(1);
  socket.onmessage?.({ data: record(WorkspaceType.PTY_STDOUT, new TextEncoder().encode('hello')) });
  // streamed read coalesced with PTY output in one datagram
  const readPromise = connection.request('read', 'a.py');
  const readRaw = JSON.parse(new TextDecoder().decode(new Uint8Array(socket.sent[socket.sent.length - 1] as ArrayBuffer).slice(6))).id as string;
  const body = new TextEncoder().encode('print(1)');
  const hex = sha256HexSync(body);
  const streaming = new Uint8Array(record(WorkspaceType.RESULT, metadataBytes({ id: readRaw, state: 'streaming', version: 'v1' })) as ArrayBuffer);
  const chunk = new Uint8Array(record(WorkspaceType.CHUNK, packChunk(readRaw, 0, body)) as ArrayBuffer);
  const end = new Uint8Array(record(WorkspaceType.END, metadataBytes({ id: readRaw, size: body.length, sha256: hex })) as ArrayBuffer);
  const out = new Uint8Array(record(WorkspaceType.PTY_STDOUT, new TextEncoder().encode('more')) as ArrayBuffer);
  const coalesced = new Uint8Array(streaming.length + chunk.length + end.length + out.length);
  coalesced.set(streaming, 0); coalesced.set(chunk, streaming.length); coalesced.set(end, streaming.length + chunk.length); coalesced.set(out, streaming.length + chunk.length + end.length);
  socket.onmessage?.({ data: msg(coalesced) });
  const readValue = await readPromise;
  await tick(10);
  expect(readValue.content).toBe('print(1)');
  expect(outputs.length).toBe(2);
  socket.onmessage?.({ data: record(WorkspaceType.PTY_EXIT, metadataBytes({ code: 0, reason: 'exited' })) });
  expect(connection.ptyState).toBe('exited');
  expect(exitInfo).toEqual({ code: 0, reason: 'exited' });
  expect(states).toEqual(['opening', 'open', 'exited']);
  expect(connection.isClosed).toBe(false);
  connection.close();
});

it('validates PTY_OPENED metadata and fails closed on unknown records', async () => {
  const { connection, socket } = await connected();
  connection.openPty(80, 24);
  socket.onmessage?.({ data: record(WorkspaceType.PTY_OPENED, metadataBytes({ protocol: 'wrong' })) });
  await tick();
  expect(connection.isClosed).toBe(true);
  const second = await connected();
  second.socket.onmessage?.({ data: record(99, new Uint8Array()) });
  await tick();
  expect(second.connection.isClosed).toBe(true);
  second.connection.close();
});

it('rejects chunk sequence mismatch, wrong request id and duplicate END', async () => {
  const { connection, socket } = await connected();
  const pending = connection.request('read', 'a.py');
  const raw = JSON.parse(new TextDecoder().decode(new Uint8Array(socket.sent[socket.sent.length - 1] as ArrayBuffer).slice(6))).id as string;
  pending.catch(() => undefined);
  socket.onmessage?.({ data: record(WorkspaceType.RESULT, metadataBytes({ id: raw, state: 'streaming' })) });
  socket.onmessage?.({ data: record(WorkspaceType.CHUNK, packChunk(raw, 7, new TextEncoder().encode('x'))) });
  await tick();
  expect(connection.isClosed).toBe(true);
  connection.close();
  const next = await connected();
  const p2 = next.connection.request('read', 'b.py');
  const raw2 = JSON.parse(new TextDecoder().decode(new Uint8Array(next.socket.sent[next.socket.sent.length - 1] as ArrayBuffer).slice(6))).id as string;
  p2.catch(() => undefined);
  next.socket.onmessage?.({ data: record(WorkspaceType.RESULT, metadataBytes({ id: raw2, state: 'streaming' })) });
  next.socket.onmessage?.({ data: record(WorkspaceType.CHUNK, packChunk('deadbeefdeadbeefdeadbeefdeadbeef', 0, new TextEncoder().encode('x'))) });
  await tick();
  expect(next.connection.isClosed).toBe(true);
  next.connection.close();
});

it('fails closed on digest mismatch', async () => {
  const { connection, socket } = await connected();
  const pending = connection.request('read', 'a.py');
  const raw = JSON.parse(new TextDecoder().decode(new Uint8Array(socket.sent[socket.sent.length - 1] as ArrayBuffer).slice(6))).id as string;
  const rejection = expect(pending).rejects.toThrow();
  socket.onmessage?.({ data: record(WorkspaceType.RESULT, metadataBytes({ id: raw, state: 'streaming' })) });
  socket.onmessage?.({ data: record(WorkspaceType.CHUNK, packChunk(raw, 0, new TextEncoder().encode('abc'))) });
  socket.onmessage?.({ data: record(WorkspaceType.END, metadataBytes({ id: raw, size: 3, sha256: '0'.repeat(64) })) });
  await rejection;
  await tick();
  expect(connection.isClosed).toBe(true);
  connection.close();
});

it('rejects only the failed request on operation errors', async () => {
  const { connection, socket } = await connected();
  const first = connection.request('stat', 'missing');
  const second = connection.request('list', '');
  const sent = socket.sent.slice(-2).map((s) => JSON.parse(new TextDecoder().decode(new Uint8Array(s as ArrayBuffer).slice(6))).id as string);
  socket.onmessage?.({ data: record(WorkspaceType.RESULT, metadataBytes({ id: sent[0], error: 'NOT_FOUND' })) });
  await expect(first).rejects.toThrow('File not found.');
  socket.onmessage?.({ data: record(WorkspaceType.RESULT, metadataBytes({ id: sent[1], state: 'ok', entries: [] })) });
  await expect(second).resolves.toBeTruthy();
  expect(connection.isClosed).toBe(false);
  connection.close();
});

it('times out hung reads and supports cancel cleanup', async () => {
  vi.useFakeTimers();
  setup();
  const connection = new WorkspaceConnection(grant);
  const done = connection.connect();
  const socket = FakeSocket.instances[FakeSocket.instances.length - 1] as FakeSocket;
  socket.onopen?.();
  socket.onmessage?.({ data: JSON.stringify({ type: 'ready', protocol: 'tcp-stream-v1' }) });
  socket.onmessage?.({ data: record(WorkspaceType.READY, metadataBytes({ protocol: 'workspace-stream-v1' })).slice(0) });
  await done;
  const pending = connection.request('read', 'slow.py', {}, 100);
  const rejection = expect(pending).rejects.toThrow('timed out');
  await vi.advanceTimersByTimeAsync(150);
  await rejection;
  expect(socket.sent.filter((s) => new Uint8Array(s as ArrayBuffer)[1] === WorkspaceType.CANCEL).length).toBe(1);
  connection.close();
  vi.useRealTimers();
});

it('close rejects every pending operation exactly once', async () => {
  const { connection } = await connected();
  const a = connection.request('list', '');
  const b = connection.request('stat', 'a.py');
  const ra = expect(a).rejects.toThrow();
  const rb = expect(b).rejects.toThrow();
  connection.close(new Error('goodbye'));
  connection.close(new Error('second is ignored'));
  await ra; await rb;
  expect(connection.isClosed).toBe(true);
});

it('records a structured disconnect diagnostic for traceability', async () => {
  const { connection, socket } = await connected();
  connection.openPty(80, 24);
  socket.onmessage?.({ data: record(WorkspaceType.PTY_OPENED, metadataBytes({ protocol: 'workspace-stream-v1' })) });
  expect(connection.disconnectInfo).toBeNull();
  // Server-side session failure surfaces as ERROR and tears the socket down.
  socket.onmessage?.({ data: record(WorkspaceType.ERROR, metadataBytes({ code: 'UNAVAILABLE' })) });
  await tick();
  expect(connection.isClosed).toBe(true);
  const info = connection.disconnectInfo;
  expect(info).not.toBeNull();
  expect(info?.pty).toBe('open');
  expect(info?.lastReceived).toMatch(/^ERROR:/);
  expect(info?.receivedRecords).toBeGreaterThan(0);
  expect(info?.sentRecords).toBeGreaterThan(0);
  connection.close();
});
