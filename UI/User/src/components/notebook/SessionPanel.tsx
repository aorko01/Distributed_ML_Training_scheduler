import React, { useEffect, useState } from 'react';
import { Cpu, HardDrive, Gauge, Terminal } from 'lucide-react';

interface SessionPanelProps {
  busy: boolean;
  uptimeSeconds: number;
  onOpenTerminal: () => void;
}

const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value));

const barClass = (pct: number) => (pct < 55 ? 'low' : pct < 80 ? 'mid' : 'high');

const formatUptime = (seconds: number) => {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m.toString().padStart(2, '0')}m`;
};

const SessionPanel: React.FC<SessionPanelProps> = ({ busy, uptimeSeconds, onOpenTerminal }) => {
  const [gpuUtil, setGpuUtil] = useState(62);
  const [vramPct, setVramPct] = useState(15);
  const [diskPct] = useState(34);

  useEffect(() => {
    const id = setInterval(() => {
      setGpuUtil((prev) => clamp(prev + (Math.random() * 24 - 12), 8, 97));
      setVramPct((prev) => clamp(prev + (Math.random() * 4 - 2), 10, 78));
    }, 2200);
    return () => clearInterval(id);
  }, []);

  const vramGb = ((vramPct / 100) * 80).toFixed(1);

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
          <div className="nb-kv"><span>Language</span><strong>Python 3.11</strong></div>
          <div className="nb-kv"><span>Stack</span><strong>PyTorch 2.3.1 · CUDA 12.1</strong></div>
          <div className="nb-kv"><span>Status</span><strong className={busy ? 'text-accent' : 'text-success'}>
            {busy ? 'Executing…' : 'Ready'}
          </strong></div>
        </section>

        <section className="nb-panel-card">
          <h5><Cpu size={13} /> Accelerator</h5>
          <div className="resource-row">
            <div className="resource-head">
              <span className="resource-label">GPU util</span>
              <span className="resource-value">{Math.round(gpuUtil)}%</span>
            </div>
            <div className="resource-bar"><div className={`resource-bar-fill ${barClass(gpuUtil)}`} style={{ width: `${gpuUtil}%` }} /></div>
          </div>
          <div className="resource-row">
            <div className="resource-head">
              <span className="resource-label">VRAM</span>
              <span className="resource-value">{vramGb} / 80 GB</span>
            </div>
            <div className="resource-bar"><div className={`resource-bar-fill ${barClass(vramPct)}`} style={{ width: `${vramPct}%` }} /></div>
          </div>
          <div className="nb-kv" style={{ marginTop: '0.75rem' }}><span>Device</span><strong>NVIDIA A100 80GB</strong></div>
        </section>

        <section className="nb-panel-card">
          <h5><HardDrive size={13} /> Machine</h5>
          <div className="nb-kv"><span>Node</span><strong>node-a100-04</strong></div>
          <div className="nb-kv"><span>Uptime</span><strong>{formatUptime(uptimeSeconds)}</strong></div>
          <div className="resource-row" style={{ marginTop: '0.6rem' }}>
            <div className="resource-head">
              <span className="resource-label">Disk</span>
              <span className="resource-value">{diskPct} / 50 GB</span>
            </div>
            <div className="resource-bar"><div className={`resource-bar-fill ${barClass(diskPct * 2)}`} style={{ width: `${diskPct * 2}%` }} /></div>
          </div>
        </section>

        <button className="btn btn-secondary nb-terminal-open-btn" onClick={onOpenTerminal}>
          <Terminal size={15} />
          Open terminal
        </button>
      </div>
    </aside>
  );
};

export default SessionPanel;
