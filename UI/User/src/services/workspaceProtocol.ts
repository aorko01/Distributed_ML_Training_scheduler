import type { ConnectionGrant } from './interactive';

const encoder = new TextEncoder();
const strictDecoder = new TextDecoder('utf-8', { fatal: true });
const lossyDecoder = new TextDecoder('utf-8');

const MAX_PAYLOAD = 65530;
const CHUNK_BYTES = 32768;
const METADATA_LIMIT = 16 * 1024;
const PARTIAL_LIMIT = 128 * 1024;
const REQUEST_TIMEOUT_MS = 30000;
const MAX_CONCURRENT_READS = 4;

export const WorkspaceType = { HELLO: 32, READY: 33, REQUEST: 34, RESULT: 35, CHUNK: 36, END: 37, PTY_OPEN: 38, PTY_OPENED: 39, PTY_STDIN: 40, PTY_STDOUT: 41, PTY_RESIZE: 42, PTY_CLOSE: 43, PTY_EXIT: 44, STATE: 45, CANCEL: 46, CLOSE: 4, ERROR: 8 } as const;

export type PtyState = 'closed' | 'opening' | 'open' | 'closing' | 'exited';

export interface WorkspaceCapabilities { protocol: 'workspace-stream-v1'; root: string; capabilities: string[]; textFileLimit: number; chunkLimit: number }

export class WorkspaceError extends Error {
  code: string; retryable: boolean; operation?: string; path?: string; closeCode?: number;
  constructor(code: string, message: string, retryable = false, operation?: string, path?: string, closeCode?: number) {
    super(message); this.name = 'WorkspaceError'; this.code = code; this.retryable = retryable; this.operation = operation; this.path = path; this.closeCode = closeCode;
  }
}

const RETRYABLE = new Set(['UNAVAILABLE', 'BUSY', 'CANCELLED', 'TIMEOUT']);
const MESSAGES: Record<string, string> = { NOT_FOUND: 'File not found.', CONFLICT: 'File changed on the server. Reload before overwriting.', READ_ONLY: 'Workspace is read-only.', UNSUPPORTED_FILE: 'Binary or unsupported file. Use the terminal to inspect it.', UNSAFE_FILE: 'Unsafe file type. Use the terminal to inspect it.', TOO_LARGE: 'File exceeds the configured size limit.', INVALID_PATH: 'Invalid path.', BUSY: 'Workspace is busy. Try again.', CANCELLED: 'Request cancelled.', UNAVAILABLE: 'Workspace unavailable. Try again.', TIMEOUT: 'Request timed out.' };

export function toWorkspaceError(code: unknown, fallback: string, operation?: string, path?: string): WorkspaceError {
  const name = typeof code === 'string' && code ? code : 'PROTOCOL_ERROR';
  return new WorkspaceError(name, MESSAGES[name] ?? fallback, RETRYABLE.has(name), operation, path);
}

export function record(type: number, payload: Uint8Array<ArrayBufferLike> = new Uint8Array()): ArrayBuffer {
  if (payload.length > MAX_PAYLOAD) throw new WorkspaceError('PROTOCOL_ERROR', 'Workspace message too large');
  const out = new Uint8Array(6 + payload.length);
  out[0] = 1; out[1] = type;
  new DataView(out.buffer).setUint32(2, payload.length);
  out.set(payload, 6);
  return out.buffer;
}

export function metadataBytes(value: unknown): Uint8Array {
  const data = encoder.encode(JSON.stringify(value));
  if (data.length > METADATA_LIMIT) throw new WorkspaceError('PROTOCOL_ERROR', 'Workspace metadata too large');
  return data;
}

export function decodeObject(value: Uint8Array): Record<string, unknown> {
  let text: string;
  try { text = strictDecoder.decode(value); } catch { throw new WorkspaceError('PROTOCOL_ERROR', 'Workspace protocol error'); }
  let parsed: unknown;
  try { parsed = JSON.parse(text); } catch { throw new WorkspaceError('PROTOCOL_ERROR', 'Workspace protocol error'); }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new WorkspaceError('PROTOCOL_ERROR', 'Workspace protocol error');
  return parsed as Record<string, unknown>;
}

export function packChunk(id: string, sequence: number, data: Uint8Array): Uint8Array {
  const name = encoder.encode(id);
  const out = new Uint8Array(1 + name.length + 4 + data.length);
  out[0] = name.length; out.set(name, 1);
  new DataView(out.buffer).setUint32(1 + name.length, sequence);
  out.set(data, 5 + name.length);
  return out;
}

function unpackChunk(value: Uint8Array): { id: string; sequence: number; data: Uint8Array } {
  const n = value[0];
  if (!n || n > 64 || value.length < n + 5) throw new WorkspaceError('PROTOCOL_ERROR', 'Workspace protocol error');
  let id: string;
  try { id = strictDecoder.decode(value.slice(1, n + 1)); } catch { throw new WorkspaceError('PROTOCOL_ERROR', 'Workspace protocol error'); }
  if (!validRequestId(id)) throw new WorkspaceError('PROTOCOL_ERROR', 'Workspace protocol error');
  const sequence = new DataView(value.buffer, value.byteOffset + n + 1, 4).getUint32(0);
  return { id, sequence, data: value.slice(n + 5) };
}

export function validRequestId(id: string): boolean { return /^[A-Za-z0-9_-]{1,64}$/.test(id); }

export function describeCloseCode(code: number): string {
  if (code === 1000) return 'Session closed';
  if (code === 1006) return 'Network connection lost';
  if (code === 4403) return 'Workspace request rejected by policy';
  if (code === 4410) return 'Workspace authorization expired';
  if (code === 1011) return 'Gateway unavailable';
  return `Workspace disconnected (code ${code})`;
}

type Pending = { resolve: (v: Record<string, unknown>) => void; reject: (e: Error) => void; operation: string; path: string; streaming: boolean; chunks: Uint8Array[]; sequence: number; timer: ReturnType<typeof setTimeout> | null; settled: boolean };

const KNOWN = new Set<number>([WorkspaceType.READY, WorkspaceType.RESULT, WorkspaceType.CHUNK, WorkspaceType.END, WorkspaceType.PTY_OPENED, WorkspaceType.PTY_STDOUT, WorkspaceType.PTY_EXIT, WorkspaceType.STATE, WorkspaceType.ERROR]);

export function sha256HexSync(data: Uint8Array): string {
  const K = [0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2];
  let h0 = 0x6a09e667, h1 = 0xbb67ae85, h2 = 0x3c6ef372, h3 = 0xa54ff53a, h4 = 0x510e527f, h5 = 0x9b05688c, h6 = 0x1f83d9ab, h7 = 0x5be0cd19;
  const bitLen = data.length * 8;
  const paddedLen = (((data.length + 8) >> 6) + 1) << 6;
  const padded = new Uint8Array(paddedLen);
  padded.set(data); padded[data.length] = 0x80;
  const view = new DataView(padded.buffer);
  view.setUint32(paddedLen - 4, bitLen >>> 0);
  view.setUint32(paddedLen - 8, Math.floor(bitLen / 4294967296));
  const w = new Uint32Array(64);
  for (let off = 0; off < paddedLen; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = view.getUint32(off + i * 4);
    for (let i = 16; i < 64; i++) {
      const s0 = ((w[i - 15] >>> 7) | (w[i - 15] << 25)) ^ ((w[i - 15] >>> 18) | (w[i - 15] << 14)) ^ (w[i - 15] >>> 3);
      const s1 = ((w[i - 2] >>> 17) | (w[i - 2] << 15)) ^ ((w[i - 2] >>> 19) | (w[i - 2] << 13)) ^ (w[i - 2] >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
    }
    let a = h0, b = h1, c = h2, d = h3, e = h4, f = h5, g = h6, h = h7;
    for (let i = 0; i < 64; i++) {
      const S1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7));
      const ch = (e & f) ^ (~e & g);
      const t1 = (h + S1 + ch + K[i] + w[i]) | 0;
      const S0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10));
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (S0 + maj) | 0;
      h = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
    }
    h0 = (h0 + a) | 0; h1 = (h1 + b) | 0; h2 = (h2 + c) | 0; h3 = (h3 + d) | 0;
    h4 = (h4 + e) | 0; h5 = (h5 + f) | 0; h6 = (h6 + g) | 0; h7 = (h7 + h) | 0;
  }
  return [h0, h1, h2, h3, h4, h5, h6, h7].map((v) => (v >>> 0).toString(16).padStart(8, '0')).join('');
}
async function sha256Hex(data: Uint8Array): Promise<string> {
  const scopeCrypto = (globalThis as unknown as { crypto?: { subtle?: { digest?: (alg: string, d: BufferSource) => Promise<ArrayBuffer> } } }).crypto;
  if (scopeCrypto?.subtle?.digest) {
    try {
      const digest = await scopeCrypto.subtle.digest('SHA-256', data as unknown as BufferSource);
      return [...new Uint8Array(digest)].map((n) => n.toString(16).padStart(2, '0')).join('');
    } catch { /* fall through to sync */ }
  }
  return sha256HexSync(data);
}


export class WorkspaceConnection {
  private socket: WebSocket | null = null;
  private partial = new Uint8Array(0);
  private pending = new Map<string, Pending>();
  private readyPromise: Promise<void> | null = null;
  private resolveReady: (() => void) | null = null;
  private rejectReady: ((e: Error) => void) | null = null;
  private readySettled = false;
  private firstError: Error | null = null;
  private closedFlag = false;
  private closeCodeValue: number | null = null;
  private epoch = 0;
  private activeReads = 0;
  private mutationChain: Promise<unknown> = Promise.resolve();
  private capabilitiesValue: WorkspaceCapabilities | null = null;
  private pty: PtyState = 'closed';
  private ptyExitInfo: { code: number; reason: string } | null = null;
  onPtyOutput: ((data: Uint8Array) => void) | null = null;
  onPtyExit: ((info: { code: number; reason: string }) => void) | null = null;
  onPtyState: ((state: PtyState) => void) | null = null;
  onState: ((state: Record<string, unknown>) => void) | null = null;
  onSocketLost: ((message: string, code: number | null) => void) | null = null;
  private grant: ConnectionGrant;
  constructor(grant: ConnectionGrant) { this.grant = grant; }
  get ptyState(): PtyState { return this.pty; }
  get ptyExit(): { code: number; reason: string } | null { return this.ptyExitInfo; }
  get serverCapabilities(): WorkspaceCapabilities | null { return this.capabilitiesValue; }
  get lastCloseCode(): number | null { return this.closeCodeValue; }
  get isClosed(): boolean { return this.closedFlag; }
  async connect(signal?: AbortSignal): Promise<void> {
    if (this.grant.protocol !== 'tcp-stream-v1' || this.grant.workspace_protocol !== 'workspace-stream-v1') throw new WorkspaceError('PROTOCOL_ERROR', 'Workspace unavailable');
    this.epoch += 1;
    const epoch = this.epoch;
    this.closedFlag = false; this.firstError = null; this.readySettled = false;
    this.readyPromise = new Promise<void>((resolve, reject) => { this.resolveReady = resolve; this.rejectReady = reject; });
    const socket = new WebSocket(this.grant.wss_url);
    socket.binaryType = 'arraybuffer';
    this.socket = socket;
    if (signal?.aborted) { this.fail(new WorkspaceError('CANCELLED', 'Connection cancelled', false), epoch); }
    signal?.addEventListener('abort', () => { this.fail(new WorkspaceError('CANCELLED', 'Connection cancelled', false), epoch); }, { once: true });
    socket.onopen = () => { if (epoch !== this.epoch || this.closedFlag) return; try { socket.send(JSON.stringify({ type: 'authenticate', ticket: this.grant.ticket })); } catch { this.fail(new WorkspaceError('UNAVAILABLE', 'Workspace connection failed', true), epoch); } };
    socket.onerror = () => { this.fail(new WorkspaceError('UNAVAILABLE', 'Workspace connection failed', true), epoch); };
    socket.onclose = (event: CloseEvent) => { const code = typeof event?.code === 'number' ? event.code : 1006; this.closeCodeValue = code; const retryable = code === 1006 || code === 1011; const err = new WorkspaceError(code === 4410 ? 'UNAVAILABLE' : 'PROTOCOL_ERROR', describeCloseCode(code), retryable, undefined, undefined, code); this.fail(err, epoch); if (this.readySettled) { try { this.onSocketLost?.(err.message, code); } catch { /* listener errors stay local */ } } };
    socket.onmessage = (event) => {
      if (epoch !== this.epoch) return;
      try {
        if (typeof event.data === 'string') {
          let gateway: unknown;
          try { gateway = JSON.parse(event.data); } catch { throw new WorkspaceError('PROTOCOL_ERROR', 'Gateway protocol error'); }
          const g = gateway as Record<string, unknown>;
          if (!g || typeof g !== 'object' || g.type !== 'ready' || g.protocol !== 'tcp-stream-v1') throw new WorkspaceError('PROTOCOL_ERROR', 'Gateway protocol error');
          this.send(WorkspaceType.HELLO, metadataBytes({ protocol: 'workspace-stream-v1' }));
          return;
        }
        this.feed(new Uint8Array(event.data as ArrayBuffer));
      } catch (error) { this.fail(error instanceof Error ? error : new WorkspaceError('PROTOCOL_ERROR', 'Workspace protocol error'), epoch); }
    };
    await this.readyPromise;
  }
  private setPty(next: PtyState): void { this.pty = next; try { this.onPtyState?.(next); } catch { /* listener errors stay local */ } }
  private fail(error: Error, epoch?: number): void {
    if (typeof epoch === 'number' && epoch !== this.epoch) return;
    if (!this.firstError) this.firstError = error;
    if (!this.readySettled) { this.readySettled = true; this.rejectReady?.(this.firstError); }
    for (const [, item] of [...this.pending]) { if (!item.settled) { item.settled = true; if (item.timer) clearTimeout(item.timer); item.reject(this.firstError); } }
    this.pending.clear(); this.activeReads = 0;
    const socket = this.socket; this.socket = null;
    if (socket) { try { socket.onopen = null; socket.onmessage = null; socket.onerror = null; socket.onclose = null; } catch { /* ignore */ } try { if (socket.readyState === WebSocket.OPEN) socket.close(); } catch { /* ignore */ } }
    this.closedFlag = true;
  }
  private settleReady(): void { if (this.readySettled) return; this.readySettled = true; this.resolveReady?.(); }
  private feed(input: Uint8Array): void {
    if (this.closedFlag) return;
    if (this.partial.length + input.length > PARTIAL_LIMIT + MAX_PAYLOAD) throw new WorkspaceError('PROTOCOL_ERROR', 'Workspace protocol error');
    const joined = new Uint8Array(this.partial.length + input.length);
    joined.set(this.partial); joined.set(input, this.partial.length);
    let offset = 0;
    while (joined.length - offset >= 6) {
      const version = joined[offset]; const type = joined[offset + 1];
      const size = new DataView(joined.buffer, joined.byteOffset + offset + 2, 4).getUint32(0);
      if (version !== 1 || size > MAX_PAYLOAD || !KNOWN.has(type)) throw new WorkspaceError('PROTOCOL_ERROR', 'Workspace protocol error');
      if (joined.length - offset < size + 6) break;
      this.receive(type, joined.slice(offset + 6, offset + 6 + size));
      if (this.closedFlag) { this.partial = new Uint8Array(0); return; }
      offset += size + 6;
    }
    const rest = joined.slice(offset);
    if (rest.length > PARTIAL_LIMIT) throw new WorkspaceError('PROTOCOL_ERROR', 'Workspace protocol error');
    this.partial = rest;
  }
  private receive(type: number, data: Uint8Array): void {
    if (type === WorkspaceType.READY) {
      const ready = decodeObject(data);
      if (ready.protocol !== 'workspace-stream-v1') throw new WorkspaceError('PROTOCOL_ERROR', 'Workspace protocol error');
      const caps = Array.isArray(ready.capabilities) ? ready.capabilities.filter((x): x is string => typeof x === 'string') : [];
      this.capabilitiesValue = { protocol: 'workspace-stream-v1', root: typeof ready.root === 'string' ? ready.root : '/workspace', capabilities: caps, textFileLimit: typeof ready.text_file_limit === 'number' ? ready.text_file_limit : 2097152, chunkLimit: typeof ready.chunk_limit === 'number' ? ready.chunk_limit : 32768 };
      this.settleReady();
      return;
    }
    if (type === WorkspaceType.PTY_OPENED) {
      const opened = decodeObject(data);
      if (opened.protocol !== 'workspace-stream-v1') throw new WorkspaceError('PROTOCOL_ERROR', 'Workspace protocol error');
      if (this.pty !== 'opening') throw new WorkspaceError('PROTOCOL_ERROR', 'Unexpected terminal reply');
      this.setPty('open');
      return;
    }
    if (type === WorkspaceType.PTY_STDOUT) {
      if (this.pty !== 'open') throw new WorkspaceError('PROTOCOL_ERROR', 'Unexpected terminal output');
      try { this.onPtyOutput?.(data.slice()); } catch { /* keep socket up */ }
      return;
    }
    if (type === WorkspaceType.PTY_EXIT) {
      const exit = decodeObject(data);
      if (typeof exit.code !== 'number' || !Number.isInteger(exit.code) || typeof exit.reason !== 'string') throw new WorkspaceError('PROTOCOL_ERROR', 'Workspace protocol error');
      if (this.pty !== 'open' && this.pty !== 'closing' && this.pty !== 'opening') throw new WorkspaceError('PROTOCOL_ERROR', 'Unexpected terminal reply');
      this.ptyExitInfo = { code: exit.code, reason: exit.reason };
      this.setPty('exited');
      try { this.onPtyExit?.({ ...this.ptyExitInfo }); } catch { /* keep socket up */ }
      return;
    }
    if (type === WorkspaceType.STATE) { const state = decodeObject(data); try { this.onState?.(state); } catch { /* keep socket up */ } return; }
    if (type === WorkspaceType.ERROR) { const body = decodeObject(data); throw toWorkspaceError(body.code, 'Workspace unavailable'); }
    if (type === WorkspaceType.RESULT) {
      const result = decodeObject(data);
      const id = result.id;
      if (typeof id !== 'string' || !validRequestId(id)) throw new WorkspaceError('PROTOCOL_ERROR', 'Workspace protocol error');
      const pending = this.pending.get(id);
      if (!pending) return;
      if (typeof result.error === 'string') { this.dropPending(id, pending); pending.reject(toWorkspaceError(result.error, String(result.error), pending.operation, pending.path)); return; }
      if (result.state === 'streaming' || result.state === 'receiving') { pending.streaming = result.state === 'streaming'; return; }
      this.dropPending(id, pending); pending.resolve(result);
      return;
    }
    if (type === WorkspaceType.CHUNK) {
      const item = unpackChunk(data);
      const pending = this.pending.get(item.id);
      if (!pending || !pending.streaming || pending.settled) throw new WorkspaceError('PROTOCOL_ERROR', 'Workspace protocol error');
      if (item.sequence !== pending.sequence) throw new WorkspaceError('PROTOCOL_ERROR', 'Workspace protocol error');
      pending.chunks.push(item.data.slice()); pending.sequence += 1;
      return;
    }
    if (type === WorkspaceType.END) {
      const end = decodeObject(data);
      const id = end.id;
      if (typeof id !== 'string' || !validRequestId(id)) throw new WorkspaceError('PROTOCOL_ERROR', 'Workspace protocol error');
      const pending = this.pending.get(id);
      if (!pending || !pending.streaming || pending.settled) throw new WorkspaceError('PROTOCOL_ERROR', 'Workspace protocol error');
      if (typeof end.size !== 'number' || !Number.isInteger(end.size) || typeof end.sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(end.sha256)) throw new WorkspaceError('PROTOCOL_ERROR', 'Workspace protocol error');
      const total = pending.chunks.reduce((n, item) => n + item.length, 0);
      if (total !== end.size) { this.dropPending(id, pending); pending.reject(new WorkspaceError('PROTOCOL_ERROR', 'Workspace protocol error', false, pending.operation, pending.path)); throw new WorkspaceError('PROTOCOL_ERROR', 'Workspace protocol error'); }
      const content = new Uint8Array(total);
      let at = 0;
      for (const item of pending.chunks) { content.set(item, at); at += item.length; }
      void sha256Hex(content).then((digest) => {
        const current = this.pending.get(id);
        if (!current || current.settled) return;
        if (digest !== (end.sha256 as string)) { this.dropPending(id, current); current.reject(new WorkspaceError('PROTOCOL_ERROR', 'Workspace protocol error', false, current.operation, current.path)); this.fail(new WorkspaceError('PROTOCOL_ERROR', 'Workspace protocol error')); return; }
        let text: string;
        try { text = strictDecoder.decode(content); } catch { this.dropPending(id, current); current.reject(toWorkspaceError('UNSUPPORTED_FILE', 'Binary file.', current.operation, current.path)); return; }
        this.dropPending(id, current); current.resolve({ ...end, content: text });
      });
      return;
    }
    throw new WorkspaceError('PROTOCOL_ERROR', 'Workspace protocol error');
  }
  private dropPending(id: string, pending: Pending): void {
    if (pending.settled) throw new WorkspaceError('PROTOCOL_ERROR', 'Workspace protocol error');
    pending.settled = true;
    if (pending.timer) clearTimeout(pending.timer);
    this.pending.delete(id);
    if (pending.operation === 'read' || pending.operation === 'list' || pending.operation === 'stat') this.activeReads = Math.max(0, this.activeReads - 1);
  }
  private send(type: number, payload?: Uint8Array<ArrayBufferLike>): void {
    const socket = this.socket;
    if (!socket || socket.readyState !== WebSocket.OPEN) throw new WorkspaceError('UNAVAILABLE', 'Workspace disconnected', true);
    socket.send(record(type, payload));
  }
  private armTimeout(id: string, timeoutMs: number, operation: string, path: string): void {
    const pending = this.pending.get(id);
    if (!pending) return;
    pending.timer = setTimeout(() => {
      const current = this.pending.get(id);
      if (!current || current.settled) return;
      current.settled = true;
      this.pending.delete(id);
      if (operation === 'read' || operation === 'list' || operation === 'stat') this.activeReads = Math.max(0, this.activeReads - 1);
      try { this.send(WorkspaceType.CANCEL, metadataBytes({ id })); } catch { /* transport gone */ }
      current.reject(new WorkspaceError('TIMEOUT', 'Request timed out.', true, operation, path));
    }, timeoutMs);
  }
  private newId(): string {
    const raw = typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID().replaceAll('-', '') : `${Date.now().toString(36)}${Math.floor(Math.random() * 1e9).toString(36)}`;
    return raw.slice(0, 32);
  }
  request(operation: string, path: string, extra: Record<string, unknown> = {}, timeoutMs = REQUEST_TIMEOUT_MS): Promise<Record<string, unknown>> {
    if (this.closedFlag) throw new WorkspaceError('UNAVAILABLE', 'Workspace disconnected', true, operation, path);
    const isRead = operation === 'read' || operation === 'list' || operation === 'stat';
    if (isRead && this.activeReads >= MAX_CONCURRENT_READS) throw new WorkspaceError('BUSY', 'Too many concurrent reads.', true, operation, path);
    const id = this.newId();
    const run = (): Promise<Record<string, unknown>> => {
      const promise = new Promise<Record<string, unknown>>((resolve, reject) => { this.pending.set(id, { resolve, reject, operation, path, streaming: operation === 'read', chunks: [], sequence: 0, timer: null, settled: false }); });
      if (isRead) this.activeReads += 1;
      this.armTimeout(id, timeoutMs, operation, path);
      this.send(WorkspaceType.REQUEST, metadataBytes({ id, operation, path, ...extra }));
      return promise;
    };
    if (isRead) return run();
    const queued = this.mutationChain.then(run, run);
    this.mutationChain = queued.then(() => undefined, () => undefined);
    return queued;
  }
  cancel(id: string): void { if (!validRequestId(id)) return; try { this.send(WorkspaceType.CANCEL, metadataBytes({ id })); } catch { /* best effort */ } }
  async write(path: string, content: string, expectedVersion: string, timeoutMs = REQUEST_TIMEOUT_MS): Promise<Record<string, unknown>> {
    if (this.closedFlag) throw new WorkspaceError('UNAVAILABLE', 'Workspace disconnected', true, 'write', path);
    const bytes = encoder.encode(content);
    if (bytes.length > 2097152) throw toWorkspaceError('TOO_LARGE', 'File exceeds the configured size limit.', 'write', path);
    const id = this.newId();
    const run = async (): Promise<Record<string, unknown>> => {
      const result = new Promise<Record<string, unknown>>((resolve, reject) => { this.pending.set(id, { resolve, reject, operation: 'write', path, streaming: false, chunks: [], sequence: 0, timer: null, settled: false }); });
      this.armTimeout(id, timeoutMs, 'write', path);
      this.send(WorkspaceType.REQUEST, metadataBytes({ id, operation: 'write', path, expected_version: expectedVersion, size: bytes.length }));
      let offset = 0; let sequence = 0;
      while (offset < bytes.length) { const data = bytes.slice(offset, offset + CHUNK_BYTES); this.send(WorkspaceType.CHUNK, packChunk(id, sequence++, data)); offset += data.length; }
      this.send(WorkspaceType.END, metadataBytes({ id, size: bytes.length, sha256: await sha256Hex(bytes) }));
      return result;
    };
    const queued = this.mutationChain.then(run, run);
    this.mutationChain = queued.then(() => undefined, () => undefined);
    return queued;
  }
  openPty(columns: number, rows: number): void {
    if (this.pty === 'opening' || this.pty === 'open') return;
    if (this.pty === 'closing') return;
    if (!Number.isInteger(columns) || !Number.isInteger(rows) || columns < 1 || columns > 500 || rows < 1 || rows > 300) throw new WorkspaceError('PROTOCOL_ERROR', 'Invalid terminal size');
    this.ptyExitInfo = null;
    this.setPty('opening');
    try { this.send(WorkspaceType.PTY_OPEN, metadataBytes({ shell: 'default', columns, rows })); }
    catch (error) { this.setPty('closed'); throw error instanceof Error ? error : new WorkspaceError('UNAVAILABLE', 'Workspace disconnected', true); }
  }
  ptyInput(data: string): void { if (this.pty !== 'open') return; if (!data) return; this.send(WorkspaceType.PTY_STDIN, encoder.encode(data)); }
  ptyBinary(data: Uint8Array): void { if (this.pty !== 'open') return; if (!data.length) return; this.send(WorkspaceType.PTY_STDIN, data); }
  resize(columns: number, rows: number): void { if (this.pty !== 'open') return; if (!Number.isInteger(columns) || !Number.isInteger(rows) || columns < 1 || columns > 500 || rows < 1 || rows > 300) return; this.send(WorkspaceType.PTY_RESIZE, metadataBytes({ columns, rows })); }
  closePty(): void {
    if (this.pty === 'closed' || this.pty === 'exited' || this.pty === 'closing') return;
    this.setPty('closing');
    try { this.send(WorkspaceType.PTY_CLOSE); } catch { /* best effort */ }
  }
  lossyText(data: Uint8Array): string { return lossyDecoder.decode(data); }
  close(error: Error = new WorkspaceError('UNAVAILABLE', 'Workspace closed')): void {
    if (!this.firstError) this.firstError = error;
    const pendingError = this.firstError;
    if (!this.readySettled) { this.readySettled = true; this.rejectReady?.(pendingError); }
    for (const [, item] of [...this.pending]) { if (!item.settled) { item.settled = true; if (item.timer) clearTimeout(item.timer); item.reject(pendingError); } }
    this.pending.clear(); this.activeReads = 0;
    const socket = this.socket; this.socket = null;
    if (socket) { try { socket.onopen = null; socket.onmessage = null; socket.onerror = null; socket.onclose = null; } catch { /* ignore */ } try { if (socket.readyState === WebSocket.OPEN) { try { socket.send(record(WorkspaceType.CLOSE)); } catch { /* best effort */ } socket.close(); } } catch { /* ignore */ } }
    this.closedFlag = true;
  }
}
