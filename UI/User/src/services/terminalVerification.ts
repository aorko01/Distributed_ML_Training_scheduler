import type { ConnectionGrant } from './interactive';

const encoder = new TextEncoder();
const decoder = new TextDecoder('utf-8', { fatal: true });
const MAX_PAYLOAD = 65530;
function protocolError(): never { throw new Error('Connection protocol error'); }

export function record(type: number, payload = new Uint8Array()): ArrayBuffer {
  if (payload.byteLength > MAX_PAYLOAD) protocolError();
  const data = new Uint8Array(6 + payload.length);
  const view = new DataView(data.buffer);
  data[0] = 1; data[1] = type; view.setUint32(2, payload.length); data.set(payload, 6);
  return data.buffer;
}

// Only flat metadata is permitted by terminal-stream-v1. Parsing members
// separately rejects duplicate keys instead of silently accepting JSON.parse's
// last value. Nested structures and trailing data are invalid.
function metadata(payload: Uint8Array): Record<string, unknown> {
  if (payload.length > 1024) protocolError();
  let source: string;
  try { source = decoder.decode(payload); } catch { return protocolError(); }
  const member = /\s*("(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*")\s*:\s*("(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null)\s*/y;
  source = source.trim();
  if (!source.startsWith('{') || !source.endsWith('}')) protocolError();
  const value: Record<string, unknown> = Object.create(null);
  let position = 1;
  if (source.slice(1, -1).trim() === '') return value;
  while (position < source.length - 1) {
    member.lastIndex = position;
    const match = member.exec(source);
    if (!match) protocolError();
    const key = JSON.parse(match[1]) as string;
    if (Object.hasOwn(value, key)) protocolError();
    value[key] = JSON.parse(match[2]) as unknown;
    position = member.lastIndex;
    if (position === source.length - 1) return value;
    if (source[position++] !== ',' || source.slice(position, -1).trim() === '') protocolError();
  }
  return protocolError();
}

export class RecordParser {
  private partial = new Uint8Array(65536);
  private size = 0;
  feed(input: Uint8Array, receive: (type: number, data: Uint8Array) => void) {
    let offset = 0;
    while (offset < input.length) {
      let target = 6;
      if (this.size >= 6) {
        const length = new DataView(this.partial.buffer).getUint32(2);
        if (this.partial[0] !== 1 || length > MAX_PAYLOAD || ![5, 6, 7, 8].includes(this.partial[1])) protocolError();
        target += length;
      }
      const take = Math.min(target - this.size, input.length - offset);
      this.partial.set(input.subarray(offset, offset + take), this.size);
      this.size += take; offset += take;
      if (this.size >= 6) {
        const length = new DataView(this.partial.buffer).getUint32(2);
        if (this.partial[0] !== 1 || length > MAX_PAYLOAD || ![5, 6, 7, 8].includes(this.partial[1])) protocolError();
        if (this.size === 6 + length) {
          receive(this.partial[1], this.partial.slice(6, this.size)); this.size = 0;
        }
      }
    }
  }
  eof() { if (this.size) protocolError(); }
}

export function verifyConnection(grant: ConnectionGrant, signal: AbortSignal,
  createSocket: (url: string) => WebSocket = url => new WebSocket(url)): Promise<void> {
  return new Promise((resolve, reject) => {
    const url = new URL(grant.wss_url);
    if (url.protocol !== 'wss:' || url.search || url.hash || url.username || url.password
        || grant.protocol !== 'tcp-stream-v1' || grant.terminal_protocol !== 'terminal-stream-v1') {
      reject(new Error('Connection configuration unavailable')); return;
    }
    if (signal.aborted) { reject(new Error('Connection cancelled')); return; }
    let socket: WebSocket;
    try { socket = createSocket(grant.wss_url); } catch { reject(new Error('Gateway unavailable')); return; }
    socket.binaryType = 'arraybuffer';
    const parser = new RecordParser();
    let phase: 'gateway' | 'opened' | 'closing' | 'finished' = 'gateway';
    let complete = false;
    let outputBytes = 0;
    const timeout = setTimeout(() => finish(new Error('Connection timed out')), 15000);
    let openTimeout: ReturnType<typeof setTimeout> | undefined;
    let closeTimeout: ReturnType<typeof setTimeout> | undefined;
    function finish(error?: Error) {
      if (complete) return;
      complete = true;
      clearTimeout(timeout); clearTimeout(openTimeout); clearTimeout(closeTimeout);
      signal.removeEventListener('abort', abort);
      socket.onopen = socket.onmessage = socket.onerror = socket.onclose = null;
      socket.close();
      if (error) reject(error); else resolve();
    }
    function abort() { finish(new Error('Connection cancelled')); }
    signal.addEventListener('abort', abort, { once: true });
    socket.onopen = () => { socket.send(JSON.stringify({ type: 'authenticate', ticket: grant.ticket })); };
    socket.onerror = () => finish(new Error('Gateway connection failed'));
    socket.onclose = () => {
      try { parser.eof(); } catch { finish(new Error('Connection protocol error')); return; }
      finish(new Error('Gateway closed the connection'));
    };
    socket.onmessage = event => {
      try {
        if (phase === 'gateway') {
          if (typeof event.data !== 'string' || encoder.encode(event.data).length > 1024) protocolError();
          const ready = metadata(encoder.encode(event.data));
          if (ready.type !== 'ready' || ready.protocol !== 'tcp-stream-v1' || Object.keys(ready).some(k => !['type','protocol'].includes(k))) protocolError();
          phase = 'opened';
          socket.send(record(1, encoder.encode(JSON.stringify({ shell: 'default', columns: 80, rows: 24 }))));
          openTimeout = setTimeout(() => finish(new Error('Workload connection timed out')), 5000);
          return;
        }
        if (!(event.data instanceof ArrayBuffer) || event.data.byteLength > 65536) protocolError();
        parser.feed(new Uint8Array(event.data), (type, payload) => {
          if (complete) return;
          if (type === 8) { metadata(payload); throw new Error('Workload connection unavailable'); }
          if (phase === 'opened') {
            if (type !== 5) protocolError();
            const opened = metadata(payload);
            if (Object.keys(opened).sort().join(',') !== 'protocol,session_id' || opened.protocol !== 'terminal-stream-v1'
                || typeof opened.session_id !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(opened.session_id)) protocolError();
            clearTimeout(openTimeout); phase = 'closing';
            socket.send(record(4));
            closeTimeout = setTimeout(() => finish(new Error('Workload session cleanup timed out')), 5000);
          } else if (phase === 'closing') {
            if (type === 6) {
              outputBytes += payload.length;
              if (outputBytes > 1024 * 1024) protocolError();
            } else if (type === 7) {
              const exit = metadata(payload);
              if (Object.keys(exit).sort().join(',') !== 'code,reason' || !Number.isInteger(exit.code) || typeof exit.reason !== 'string') protocolError();
              phase = 'finished';
            } else protocolError();
          } else protocolError();
        });
        if (phase === 'finished') { parser.eof(); finish(); }
      } catch (error) { finish(error instanceof Error ? error : new Error('Connection protocol error')); }
    };
  });
}
