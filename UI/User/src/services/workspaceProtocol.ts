import type { ConnectionGrant } from './interactive';

const encoder = new TextEncoder();
const decoder = new TextDecoder('utf-8', { fatal: true });
const MAX = 65530; const CHUNK = 32768;
export const WorkspaceType = { HELLO: 32, READY: 33, REQUEST: 34, RESULT: 35, CHUNK: 36, END: 37, PTY_OPEN: 38, PTY_OPENED: 39, PTY_STDIN: 40, PTY_STDOUT: 41, PTY_RESIZE: 42, PTY_CLOSE: 43, PTY_EXIT: 44, STATE: 45, CANCEL: 46, CLOSE: 4, ERROR: 8 } as const;

function record(type: number, payload: Uint8Array<ArrayBufferLike> = new Uint8Array()) { if (payload.length > MAX) throw new Error('Workspace message too large'); const out = new Uint8Array(6 + payload.length); const view = new DataView(out.buffer); out[0] = 1; out[1] = type; view.setUint32(2, payload.length); out.set(payload, 6); return out.buffer; }
function json(value: unknown) { const data = encoder.encode(JSON.stringify(value)); if (data.length > 16 * 1024) throw new Error('Workspace metadata too large'); return data; }
function decode(value: Uint8Array): Record<string, unknown> { let parsed: unknown; try { parsed = JSON.parse(decoder.decode(value)); } catch { throw new Error('Workspace protocol error'); } if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Workspace protocol error'); return parsed as Record<string, unknown>; }
function chunk(id: string, sequence: number, data: Uint8Array) { const name = encoder.encode(id); const output = new Uint8Array(1 + name.length + 4 + data.length); output[0] = name.length; output.set(name, 1); new DataView(output.buffer).setUint32(1 + name.length, sequence); output.set(data, 5 + name.length); return output; }
function parseChunk(value: Uint8Array) { const n = value[0]; if (!n || value.length < n + 5) throw new Error('Workspace protocol error'); return { id: decoder.decode(value.slice(1, n + 1)), sequence: new DataView(value.buffer, value.byteOffset + n + 1, 4).getUint32(0), data: value.slice(n + 5) }; }

type Pending = { resolve: (value: Record<string, unknown>) => void; reject: (reason: Error) => void; chunks?: Uint8Array[]; sequence?: number };
export class WorkspaceConnection {
  private socket!: WebSocket; private partial = new Uint8Array(); private pending = new Map<string, Pending>(); private ready!: Promise<void>; private resolveReady!: () => void; private rejectReady!: (e: Error) => void;
  onPtyOutput?: (data: Uint8Array) => void; onPtyExit?: () => void; onState?: (state: Record<string, unknown>) => void;
  private grant: ConnectionGrant;
  constructor(grant: ConnectionGrant) { this.grant = grant; }
  async connect() {
    if (this.grant.protocol !== 'tcp-stream-v1' || this.grant.workspace_protocol !== 'workspace-stream-v1') throw new Error('Workspace unavailable');
    this.ready = new Promise<void>((resolve, reject) => { this.resolveReady = resolve; this.rejectReady = reject; });
    this.socket = new WebSocket(this.grant.wss_url); this.socket.binaryType = 'arraybuffer';
    this.socket.onopen = () => this.socket.send(JSON.stringify({ type: 'authenticate', ticket: this.grant.ticket }));
    this.socket.onerror = () => this.close(new Error('Workspace connection failed'));
    this.socket.onclose = () => this.close(new Error('Workspace disconnected'));
    this.socket.onmessage = event => { try { if (typeof event.data === 'string') { const gateway = JSON.parse(event.data); if (gateway.type !== 'ready' || gateway.protocol !== 'tcp-stream-v1') throw new Error('Gateway protocol error'); this.send(WorkspaceType.HELLO, json({ protocol: 'workspace-stream-v1' })); return; } this.feed(new Uint8Array(event.data)); } catch (error) { this.close(error instanceof Error ? error : new Error('Workspace protocol error')); } };
    await this.ready;
  }
  private feed(input: Uint8Array) { const joined = new Uint8Array(this.partial.length + input.length); joined.set(this.partial); joined.set(input, this.partial.length); let offset = 0; while (joined.length - offset >= 6) { const view = new DataView(joined.buffer, joined.byteOffset + offset); if (view.getUint8(0) !== 1) throw new Error('Workspace protocol error'); const type = view.getUint8(1); const size = view.getUint32(2); if (size > MAX || joined.length - offset < size + 6) break; this.receive(type, joined.slice(offset + 6, offset + 6 + size)); offset += size + 6; } this.partial = joined.slice(offset); }
  private receive(type: number, data: Uint8Array) { if (type === WorkspaceType.READY) { decode(data); this.resolveReady(); return; } if (type === WorkspaceType.PTY_STDOUT) return this.onPtyOutput?.(data); if (type === WorkspaceType.PTY_EXIT) return this.onPtyExit?.(); if (type === WorkspaceType.STATE) return this.onState?.(decode(data)); if (type === WorkspaceType.ERROR) throw new Error(String(decode(data).code ?? 'Workspace unavailable'));
    if (type === WorkspaceType.RESULT) { const result = decode(data); const id = result.id; if (typeof id !== 'string') throw new Error('Workspace protocol error'); const pending = this.pending.get(id); if (!pending) return; if (typeof result.error === 'string') { this.pending.delete(id); pending.reject(new Error(result.error)); } else if (result.state !== 'streaming' && result.state !== 'receiving') { this.pending.delete(id); pending.resolve(result); } return; }
    if (type === WorkspaceType.CHUNK) { const item = parseChunk(data); const pending = this.pending.get(item.id); if (!pending || pending.sequence !== item.sequence) throw new Error('Workspace protocol error'); pending.chunks!.push(item.data); pending.sequence!++; return; }
    if (type === WorkspaceType.END) { const end = decode(data); const id = end.id; if (typeof id !== 'string') throw new Error('Workspace protocol error'); const pending = this.pending.get(id); if (!pending) throw new Error('Workspace protocol error'); this.pending.delete(id); const content = new Uint8Array(pending.chunks!.reduce((n, item) => n + item.length, 0)); let at = 0; for (const item of pending.chunks!) { content.set(item, at); at += item.length; } pending.resolve({ ...end, content: decoder.decode(content) }); return; }
    throw new Error('Workspace protocol error');
  }
  private send(type: number, payload?: Uint8Array<ArrayBufferLike>) { if (this.socket.readyState !== WebSocket.OPEN) throw new Error('Workspace disconnected'); this.socket.send(record(type, payload)); }
  request(operation: string, path: string, extra: Record<string, unknown> = {}) { const id = crypto.randomUUID().replaceAll('-', ''); const promise = new Promise<Record<string, unknown>>((resolve, reject) => this.pending.set(id, { resolve, reject, chunks: operation === 'read' ? [] : undefined, sequence: 0 })); this.send(WorkspaceType.REQUEST, json({ id, operation, path, ...extra })); return promise; }
  async write(path: string, content: string, expected_version: string) { const id = crypto.randomUUID().replaceAll('-', ''); const bytes = encoder.encode(content); const result = new Promise<Record<string, unknown>>((resolve, reject) => this.pending.set(id, { resolve, reject })); this.send(WorkspaceType.REQUEST, json({ id, operation: 'write', path, expected_version, size: bytes.length })); let offset = 0, sequence = 0; while (offset < bytes.length) { const data = bytes.slice(offset, offset + CHUNK); this.send(WorkspaceType.CHUNK, chunk(id, sequence++, data)); offset += data.length; } const hash = await crypto.subtle.digest('SHA-256', bytes); this.send(WorkspaceType.END, json({ id, size: bytes.length, sha256: [...new Uint8Array(hash)].map(n => n.toString(16).padStart(2, '0')).join('') })); return result; }
  openPty(columns: number, rows: number) { this.send(WorkspaceType.PTY_OPEN, json({ shell: 'default', columns, rows })); }
  ptyInput(data: string) { this.send(WorkspaceType.PTY_STDIN, encoder.encode(data)); }
  resize(columns: number, rows: number) { this.send(WorkspaceType.PTY_RESIZE, json({ columns, rows })); }
  close(error = new Error('Workspace closed')) { if (this.socket && this.socket.readyState === WebSocket.OPEN) { try { this.send(WorkspaceType.CLOSE); } catch {} this.socket.close(); } this.rejectReady?.(error); for (const pending of this.pending.values()) pending.reject(error); this.pending.clear(); }
}
