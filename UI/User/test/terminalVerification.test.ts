import { afterEach, expect, it, vi } from 'vitest';
import { verifyConnection, record, RecordParser } from '../src/services/terminalVerification';
import type { ConnectionGrant } from '../src/services/interactive';
const grant: ConnectionGrant = { wss_url: 'wss://gateway.example/v1/connect/runtime/terminal', ticket: 'private', expires_at: 'later', runtime_id: 'runtime', generation: 1, protocol: 'tcp-stream-v1', terminal_protocol: 'terminal-stream-v1' };
const json = (type: number, value: object) => record(type, new TextEncoder().encode(JSON.stringify(value)));
class Socket {
  binaryType = ''; onopen: (() => void) | null = null; onmessage: ((event: {data: unknown}) => void) | null = null;
  onerror: (() => void) | null = null; onclose: ((event: {code: number}) => void) | null = null;
  sent: unknown[] = []; closed = false;
  send(value: unknown) { this.sent.push(value); }
  close() { this.closed = true; }
  message(data: unknown) { this.onmessage?.({data}); }
  closeRemote(code: number) { this.onclose?.({code}); }
}
afterEach(() => vi.useRealTimers());
it('requires workload OPENED and closes only the verification session', async () => {
  const socket = new Socket(); let resolved = false;
  const check = verifyConnection(grant, new AbortController().signal, () => socket as unknown as WebSocket).then(() => { resolved = true; });
  expect(socket.binaryType).toBe('arraybuffer'); socket.onopen?.();
  expect(JSON.parse(socket.sent[0] as string)).toEqual({type:'authenticate',ticket:'private'});
  await Promise.resolve(); expect(resolved).toBe(false);
  socket.message('{"type":"ready","protocol":"tcp-stream-v1"}');
  await Promise.resolve(); expect(resolved).toBe(false);
  const opened = new Uint8Array(json(5,{session_id:'opaque',protocol:'terminal-stream-v1'}));
  socket.message(opened.slice(0,3).buffer); socket.message(opened.slice(3,8).buffer); socket.message(opened.slice(8).buffer);
  expect(new Uint8Array(socket.sent[2] as ArrayBuffer)[1]).toBe(4);
  expect(socket.closed).toBe(false);
  const output = new Uint8Array(record(6,new TextEncoder().encode('banner')));
  const exit = new Uint8Array(json(7,{code:0,reason:'closed'}));
  const coalesced = new Uint8Array(output.length+exit.length); coalesced.set(output);coalesced.set(exit,output.length);
  socket.message(coalesced.buffer);await check;
  expect(resolved).toBe(true);expect(socket.closed).toBe(true);
});
it.each(['{"session_id":"opaque","session_id":"duplicate","protocol":"terminal-stream-v1"}', '{"session_id":"opaque","protocol":"wrong"}'])('rejects invalid OPENED metadata', async payload => {
  const socket = new Socket(); const check = verifyConnection(grant,new AbortController().signal,()=>socket as unknown as WebSocket);
  const rejection = expect(check).rejects.toThrow('protocol');
  socket.message('{"type":"ready","protocol":"tcp-stream-v1"}');
  socket.message(record(5,new TextEncoder().encode(payload)));await rejection;expect(socket.closed).toBe(true);
});
it('times out on Gateway ready alone and aborts on navigation', async () => {  vi.useFakeTimers();const socket = new Socket(); const controller = new AbortController();
  const check = verifyConnection(grant,controller.signal,()=>socket as unknown as WebSocket);
  const rejection = expect(check).rejects.toThrow('timed out');
  socket.message('{"type":"ready","protocol":"tcp-stream-v1"}');await vi.advanceTimersByTimeAsync(5001);await rejection;
  const other=new Socket();const cancelled=verifyConnection(grant,controller.signal,()=>other as unknown as WebSocket);
  const rejected=expect(cancelled).rejects.toThrow('cancelled');controller.abort();await rejected;expect(other.closed).toBe(true);
});
it('bounds unknown and partial records', () => {
  const parser=new RecordParser();expect(()=>parser.feed(new Uint8Array(record(99)),()=>{})).toThrow();
  const partial=new RecordParser();partial.feed(new Uint8Array([1,5]),()=>{});expect(()=>partial.eof()).toThrow();
});
it('reports the gateway close code instead of a generic message', async () => {
  const socket = new Socket();
  const check = verifyConnection(grant, new AbortController().signal, () => socket as unknown as WebSocket);
  const rejection = expect(check).rejects.toThrow('Gateway closed the connection (code 4403)');
  socket.closeRemote(4403); await rejection; expect(socket.closed).toBe(true);
});
