import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { interactive } from '../../services/interactive';
import { getUsername } from '../../services/api';

interface Toast { id: string; title: string; body: string; workspaceId: string }

function loadSeen(user: string): Record<string, string> {
  try {
    return JSON.parse(sessionStorage.getItem(`interactive-seen-${user}`) ?? '{}');
  } catch {
    return {};
  }
}

function saveSeen(user: string, value: Record<string, string>) {
  try {
    const keys = Object.keys(value).slice(-200);
    const trimmed: Record<string, string> = {};
    for (const k of keys) trimmed[k] = value[k];
    sessionStorage.setItem(`interactive-seen-${user}`, JSON.stringify(trimmed));
  } catch {
    /* storage unavailable: notifications still render inline */
  }
}

export function RuntimeWatcher() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seen = useRef<Record<string, string>>({});
  const user = useRef<string>('');

  useEffect(() => {
    user.current = getUsername() ?? 'anonymous';
    seen.current = loadSeen(user.current);
    let active = true;
    let timer: ReturnType<typeof setInterval>;
    async function poll() {
      if (document.hidden) return;
      try {
        const runtimes = await interactive.mine();
        if (!active) return;
        const next: Toast[] = [];
        for (const r of runtimes) {
          const key = `${r.id}:${r.generation}:${r.state}`;
          const last = seen.current[r.id];
          if (last === key) continue;
          seen.current[r.id] = key;
          const ws = r.workspace_name ?? 'workspace';
          if (r.state === 'ASSIGNED' && r.desired_state === 'RUNNING') {
            next.push({ id: `${r.id}-assigned`, title: 'Machine assigned', body: `A matching machine has been assigned to ${ws}.`, workspaceId: r.workspace_id });
          } else if (r.state === 'READY') {
            next.push({ id: `${r.id}-ready`, title: 'Workspace ready', body: `${ws} is ready.`, workspaceId: r.workspace_id });
          } else if (r.state === 'FAILED' || r.state === 'LOST') {
            next.push({ id: `${r.id}-failed`, title: 'Runtime failed', body: `${ws}: ${r.failure_detail ?? r.state}.`, workspaceId: r.workspace_id });
          }
        }
        if (next.length > 0) {
          saveSeen(user.current, seen.current);
          setToasts((prev) => [...next.slice(-3), ...prev].slice(0, 3));
        }
      } catch {
        /* polling failures are ignored; details page stays authoritative */
      }
    }
    void poll();
    timer = setInterval(poll, 5000);
    return () => { active = false; clearInterval(timer); };
  }, []);

  if (toasts.length === 0) return null;
  return (
    <div aria-live="polite" aria-label="Runtime notifications" className="runtime-toasts">
      {toasts.map((t) => (
        <div key={t.id} role="status" className="card runtime-toast">
          <strong>{t.title}</strong>
          <p>{t.body}</p>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <Link className="btn btn-secondary" to={`/interactive/${t.workspaceId}`}>View details</Link>
            {t.id.endsWith('-ready') && <Link className="btn btn-primary" to={`/interactive/${t.workspaceId}/editor`}>Open Editor</Link>}
            <button className="btn btn-secondary" onClick={() => setToasts((prev) => prev.filter((x) => x.id !== t.id))}>Dismiss</button>
          </div>
        </div>
      ))}
    </div>
  );
}
