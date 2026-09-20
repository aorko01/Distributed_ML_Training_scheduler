import { useCallback, useEffect, useRef } from 'react';
import { interactive } from '../../../services/interactive';
import { WorkspaceConnection, WorkspaceError, describeCloseCode } from '../../../services/workspaceProtocol';

interface Args {
  workspaceId: string;
  maxRetries?: number;
  onGrantMismatch: () => void;
  onConnected: (conn: WorkspaceConnection, runtimeId: string) => void;
  onDisconnected: (message: string, code: number | null, retryable: boolean) => void;
  onReconnecting: (attempt: number, delayMs: number) => void;
  onState: (state: Record<string, unknown>) => void;
  onPtyOutput: (data: Uint8Array) => void;
  onPtyExit: (info: { code: number; reason: string }) => void;
  onPtyState: (s: 'closed' | 'opening' | 'open' | 'closing' | 'exited') => void;
}

export function backoffDelay(attempt: number): number {
  const base = Math.min(15000, 1000 * 2 ** Math.max(0, attempt - 1));
  return Math.round(base * (0.7 + Math.random() * 0.6));
}

/** Connection with epoch fencing, per-attempt AbortController, bounded auto-reconnect. */
export function useWorkspaceConnection(args: Args) {
  const connRef = useRef<WorkspaceConnection | null>(null);
  const epochRef = useRef(0);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const stateRef = useRef(args);
  stateRef.current = args;
  const clearTimers = useCallback(() => { for (const t of timers.current) clearTimeout(t); timers.current = []; }, []);

  const disconnect = useCallback(() => {
    epochRef.current += 1;
    clearTimers();
    try { connRef.current?.close(); } catch { /* ignore */ }
    connRef.current = null;
  }, [clearTimers]);

  useEffect(() => () => { epochRef.current += 1; clearTimers(); try { connRef.current?.close(); } catch { /* ignore */ } connRef.current = null; }, [clearTimers]);

  const attemptConnect = useCallback(async (epoch: number, signal: AbortSignal) => {
    const wid = stateRef.current.workspaceId;
    const [item, running] = await Promise.all([interactive.detail(wid), interactive.runtime(wid)]);
    if (epoch !== epochRef.current || signal.aborted) return;
    if (!running || running.editor_capable !== true || running.state !== 'READY' || running.desired_state !== 'RUNNING' || running.access_service !== 'workspace') {
      throw new WorkspaceError('PROTOCOL_ERROR', 'This runtime does not support the workspace editor. Start a new editor-capable runtime.');
    }
    const cur = stateRef.current;
    cur.onGrantMismatch();
    void item;
    const grant = await interactive.workspaceConnection(running.id, signal);
    if (epoch !== epochRef.current || signal.aborted) return;
    if (grant.runtime_id !== running.id || grant.generation !== running.generation) throw new WorkspaceError('PROTOCOL_ERROR', 'Runtime changed during connection request', true);
    const conn = new WorkspaceConnection(grant);
    conn.onState = (s) => { if (epoch === epochRef.current) stateRef.current.onState(s); };
    conn.onPtyOutput = (d) => { if (epoch === epochRef.current) stateRef.current.onPtyOutput(d); };
    conn.onPtyExit = (i) => { if (epoch === epochRef.current) stateRef.current.onPtyExit(i); };
    conn.onPtyState = (s) => { if (epoch === epochRef.current) stateRef.current.onPtyState(s); };
    conn.onSocketLost = (msg, code) => { if (epoch === epochRef.current) stateRef.current.onDisconnected(msg, code, true); };
    connRef.current = conn;
    await conn.connect(signal);
    if (epoch !== epochRef.current) { try { conn.close(); } catch { /* ignore */ } return; }
    stateRef.current.onConnected(conn, running.id);
  }, []);

  const connectOnce = useCallback(async (attempt: number) => {
    void attempt;
    const epoch = epochRef.current + 1;
    epochRef.current = epoch;
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 20000);
    timers.current.push(timer);
    try {
      await attemptConnect(epoch, ctl.signal);
    } catch (err) {
      if (epoch !== epochRef.current) return;
      if (ctl.signal.aborted) {
        stateRef.current.onDisconnected('Connection attempt timed out', null, true);
        return;
      }
      const e = err as Error & { code?: string; closeCode?: number };
      const code = typeof e?.closeCode === 'number' ? e.closeCode : null;
      const retryable = (e instanceof WorkspaceError && e.retryable) || (code !== null && code !== 4403 && code !== 4401);
      stateRef.current.onDisconnected(err instanceof Error ? err.message : 'Editor unavailable', code, Boolean(retryable));
    } finally { clearTimeout(timer); }
  }, [attemptConnect]);

  const autoReconnect = useCallback(async (firstAttempt: number) => {
    // Single bounded wait-then-retry; the caller caps attempts at 5 and
    // connectOnce re-fences by epoch, so a stale timer cannot resurrect a
    // dead socket.
    const delay = backoffDelay(firstAttempt);
    stateRef.current.onReconnecting(firstAttempt, delay);
    await new Promise<void>((resolve) => { const t = setTimeout(resolve, delay); timers.current.push(t); });
    await connectOnce(firstAttempt);
  }, [connectOnce]);

  return { connRef, epochRef, connectOnce, autoReconnect, disconnect, describeClose: describeCloseCode };
}
