import React, { useEffect, useMemo, useState } from 'react';
import {
  Server,
  Gpu,
  HardDrive,
  MemoryStick,
  Cpu,
  Activity,
  CalendarClock,
  Loader2,
  X,
  CheckCircle2,
  Zap,
} from 'lucide-react';
import { fetchAllNodes, acquireMachine, type WorkerNode } from '../services/workers';

const statusBadge = (status: WorkerNode['status']) => (
  <span className={`badge ${status === 'online' ? 'badge-online' : 'badge-offline'}`}>
    <span className="status-dot" />
    {status}
  </span>
);

interface ResourceBarProps {
  label: string;
  icon: React.ReactNode;
  usedPercent: number;
  details: string;
}

const ResourceBar: React.FC<ResourceBarProps> = ({ label, icon, usedPercent, details }) => {
  const clamped = Math.min(100, Math.max(0, usedPercent));
  const level = clamped >= 85 ? 'high' : clamped >= 60 ? 'mid' : 'low';
  return (
    <div className="resource-row">
      <div className="resource-head">
        <span className="resource-label">{icon}{label}</span>
        <span className="resource-value">{details}</span>
      </div>
      <div className="resource-bar">
        <div className={`resource-bar-fill ${level}`} style={{ width: `${clamped}%` }} />
      </div>
    </div>
  );
};

const Machines: React.FC = () => {
  const [nodes, setNodes] = useState<WorkerNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<WorkerNode | null>(null);
  const [duration, setDuration] = useState(4);
  const [gpus, setGpus] = useState(1);
  const [submitting, setSubmitting] = useState(false);
  const [acquiredNote, setAcquiredNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const loadData = async () => {
      try {
        const data = await fetchAllNodes();
        if (!cancelled) setNodes(data);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    loadData();
    return () => {
      cancelled = true;
    };
  }, []);

  const stats = useMemo(() => {
    const online = nodes.filter((n) => n.status === 'online');
    return {
      online: online.length,
      total: nodes.length,
      gpus: nodes.reduce((sum, n) => sum + n.num_gpus, 0),
      vram: nodes.reduce((sum, n) => sum + n.total_vram, 0),
      runningJobs: nodes.reduce((sum, n) => sum + n.running_jobs, 0),
    };
  }, [nodes]);

  const openAcquire = (node: WorkerNode) => {
    const freeGpus = Math.max(0, node.num_gpus - node.gpus_in_use);
    setGpus(Math.min(1, freeGpus || 1));
    setDuration(4);
    setAcquiredNote(null);
    setError(null);
    setSelected(node);
  };

  const closeAcquire = () => {
    if (submitting) return;
    setSelected(null);
  };

  const isAcquirable = (node: WorkerNode) =>
    node.status === 'online' && node.gpus_in_use < node.num_gpus;

  const availableGpus = (node: WorkerNode) =>
    Math.max(0, node.num_gpus - node.gpus_in_use);

  const diskUsedPercent = (node: WorkerNode) =>
    node.total_disk > 0 ? ((node.total_disk - node.available_disk) / node.total_disk) * 100 : 0;

  const vramUsedPercent = (node: WorkerNode) =>
    node.total_vram > 0 ? ((node.total_vram - node.available_vram) / node.total_vram) * 100 : 0;

  const handleAcquire = async () => {
    if (!selected) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await acquireMachine({
        workerId: selected.worker_id,
        hostname: selected.hostname,
        gpus,
        durationHours: Math.max(1, Math.round(duration)),
      });
      setAcquiredNote(res.message);
      setNodes((prev) =>
        prev.map((n) =>
          n.worker_id === selected.worker_id
            ? { ...n, running_jobs: n.running_jobs + 1, gpus_in_use: n.gpus_in_use + gpus }
            : n,
        ),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to request acquisition.');
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
        <Loader2 className="animate-spin text-blue-500" size={32} />
      </div>
    );
  }

  return (
    <div className="fade-in">
      <h1>Acquire Machines</h1>

      <div className="card demo-banner">
        <Zap size={18} color="var(--status-pending)" />
        <p style={{ margin: 0 }}>
          Machine status is shown below with sample data. Acquiring a machine for experiments is
          coming soon — the reservation flow shown here is a preview and has no effect yet.
        </p>
      </div>

      <div className="metrics-grid">
        <div className="metric-card">
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <div className="metric-title">Online Nodes</div>
            <Server size={20} color="var(--text-secondary)" />
          </div>
          <div className="metric-value">{stats.online}<span className="metric-sub"> / {stats.total}</span></div>
        </div>
        <div className="metric-card">
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <div className="metric-title">Total GPUs</div>
            <Gpu size={20} color="var(--text-secondary)" />
          </div>
          <div className="metric-value">{stats.gpus}</div>
        </div>
        <div className="metric-card">
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <div className="metric-title">Total VRAM</div>
            <MemoryStick size={20} color="var(--text-secondary)" />
          </div>
          <div className="metric-value">{stats.vram}<span className="metric-sub"> GB</span></div>
        </div>
        <div className="metric-card">
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <div className="metric-title">Jobs Running</div>
            <Activity size={20} color="var(--text-secondary)" />
          </div>
          <div className="metric-value">{stats.runningJobs}</div>
        </div>
      </div>

      <h2 style={{ marginBottom: '1rem' }}>Worker Nodes</h2>
      <div className="machine-grid">
        {nodes.map((node) => (
          <div key={node.worker_id} className={`machine-card ${node.status === 'offline' ? 'machine-offline' : ''}`}>
            <div className="machine-card-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                <div className="machine-icon"><Server size={20} /></div>
                <div>
                  <div className="machine-hostname">{node.hostname}</div>
                  <div className="machine-ip">{node.ip_address}</div>
                </div>
              </div>
              {statusBadge(node.status)}
            </div>

            <div className="machine-specs">
              <span><Gpu size={14} /> {node.gpu_type} x {node.num_gpus}</span>
              <span><Activity size={14} /> {node.gpus_in_use}/{node.num_gpus} GPUs in use</span>
            </div>

            <div className="resource-list">
              <ResourceBar
                label="VRAM"
                icon={<MemoryStick size={14} />}
                usedPercent={vramUsedPercent(node)}
                details={`${node.available_vram.toFixed(0)} / ${node.total_vram.toFixed(0)} GB free`}
              />
              <ResourceBar
                label="GPU Load"
                icon={<Gpu size={14} />}
                usedPercent={node.gpu_load}
                details={`${node.gpu_load.toFixed(0)}%`}
              />
              <ResourceBar
                label="RAM"
                icon={<Cpu size={14} />}
                usedPercent={node.mem_usage}
                details={`${node.mem_usage.toFixed(0)}% used`}
              />
              <ResourceBar
                label="Disk"
                icon={<HardDrive size={14} />}
                usedPercent={diskUsedPercent(node)}
                details={`${node.available_disk.toFixed(0)} / ${node.total_disk.toFixed(0)} GB free`}
              />
            </div>

            <div className="machine-footer">
              <span style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', color: 'var(--text-secondary)', fontSize: '0.875rem' }}>
                <Activity size={14} /> {node.running_jobs} running {node.running_jobs === 1 ? 'job' : 'jobs'}
              </span>
              <button
                className={`btn ${isAcquirable(node) ? 'btn-primary' : 'btn-secondary'}`}
                style={{ padding: '0.5rem 1rem' }}
                disabled={!isAcquirable(node)}
                onClick={() => openAcquire(node)}
              >
                Acquire
              </button>
            </div>
          </div>
        ))}
      </div>

      {selected && (
        <div className="modal-overlay" onClick={closeAcquire}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3 style={{ margin: 0 }}>Acquire Machine</h3>
              <button className="modal-close" onClick={closeAcquire} disabled={submitting} aria-label="Close">
                <X size={18} />
              </button>
            </div>

            <div className="modal-body">
              <div className="modal-machine">
                <div className="machine-icon"><Server size={20} /></div>
                <div>
                  <div className="machine-hostname">{selected.hostname}</div>
                  <div className="machine-ip">{selected.ip_address} · {selected.gpu_type}</div>
                </div>
              </div>

              {acquiredNote ? (
                <div className="acquire-success">
                  <CheckCircle2 size={20} color="var(--status-success)" />
                  <p>{acquiredNote}</p>
                </div>
              ) : (
                <>
                  <div className="form-group">
                    <label className="form-label">GPUs to acquire</label>
                    <select
                      className="form-select"
                      value={gpus}
                      onChange={(e) => setGpus(Number(e.target.value))}
                    >
                      {Array.from({ length: availableGpus(selected) }, (_, i) => i + 1).map((n) => (
                        <option key={n} value={n}>{n}</option>
                      ))}
                    </select>
                  </div>
                  <div className="form-group">
                    <label className="form-label">Duration (hours)</label>
                    <input
                      className="form-input"
                      type="number"
                      min={1}
                      max={168}
                      value={duration}
                      onChange={(e) => setDuration(Number(e.target.value))}
                    />
                  </div>
                  {error && <p className="error-text">{error}</p>}
                </>
              )}
            </div>

            <div className="modal-footer">
              {acquiredNote ? (
                <button className="btn btn-secondary" style={{ width: '100%' }} onClick={closeAcquire}>
                  Close
                </button>
              ) : (
                <div style={{ display: 'flex', gap: '0.75rem', width: '100%' }}>
                  <button className="btn btn-secondary" style={{ flex: 1 }} onClick={closeAcquire} disabled={submitting}>
                    Cancel
                  </button>
                  <button className="btn btn-primary" style={{ flex: 2 }} onClick={handleAcquire} disabled={submitting}>
                    {submitting ? (
                      <Loader2 className="animate-spin" size={18} />
                    ) : (
                      <CalendarClock size={18} />
                    )}
                    {submitting ? 'Requesting...' : 'Acquire Machine'}
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default Machines;