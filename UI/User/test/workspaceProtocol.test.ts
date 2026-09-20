import { afterEach, expect, it, vi } from 'vitest';
import { WorkspaceConnection } from '../src/services/workspaceProtocol';
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

afterEach(() => { vi.unstubAllGlobals(); FakeSocket.instances = []; });

it('reports the gateway close code on early disconnect', async () => {
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket);
  const connection = new WorkspaceConnection(grant);
  const check = connection.connect();
  const rejection = expect(check).rejects.toThrow('Workspace disconnected (code 4403)');
  const socket = FakeSocket.instances[0] as FakeSocket;
  socket.onclose?.({ code: 4403 });
  await rejection;
});
