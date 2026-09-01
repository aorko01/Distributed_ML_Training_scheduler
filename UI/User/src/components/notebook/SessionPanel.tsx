import React from 'react';
import { Cpu, HardDrive, Gauge } from 'lucide-react';

interface SessionPanelProps {
  busy: boolean;
  uptimeSeconds: number;
}

const formatUptime = (seconds: number) => {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m.toString().padStart(2, '0')}m`;
};

const SessionPanel: React.FC<SessionPanelProps> = ({ busy, uptimeSeconds }) => {
  return (
    <aside className="nb-session">
      <div className="nb-explorer-head">
        <span className="nb-rail-label">Session</span>
        <span className={`badge ${busy ? 'badge-running' : 'badge-success'}`} style={{ textTransform: 'none' }}>
          {busy ? 'Busy' : 'Idle'}
        </span>
      </div>

      <div className="nb-session-body">
        <section className="nb-panel-card">
          <h5><Gauge size={13} /> Kernel</h5>
          <div className="nb-kv"><span>Status</span><strong className={busy ? 'text-accent' : 'text-success'}>
            {busy ? 'Executing…' : 'Ready'}
          </strong></div>
        </section>

        <section className="nb-panel-card">
          <h5><Cpu size={13} /> Accelerator</h5>
          <p style={{ fontSize: '0.82rem', color: 'var(--text-secondary)' }}>
            No GPU metrics available yet.
          </p>
        </section>

        <section className="nb-panel-card">
          <h5><HardDrive size={13} /> Machine</h5>
          <div className="nb-kv"><span>Uptime</span><strong>{formatUptime(uptimeSeconds)}</strong></div>
        </section>
      </div>
    </aside>
  );
};

export default SessionPanel;
