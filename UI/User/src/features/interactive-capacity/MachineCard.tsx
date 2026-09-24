import React from 'react';
import { Server, Gpu, HardDrive, MemoryStick, Cpu, Activity } from 'lucide-react';
import type { CapacityMachine } from '../../services/interactive';
import { workloadLabel } from './requirements';

const ResourceBar: React.FC<{ label: string; icon: React.ReactNode; usedPercent: number; details: string }> = ({ label, icon, usedPercent, details }) => {
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

export const MachineCard: React.FC<{ machine: CapacityMachine }> = ({ machine }) => {
  const vramUsed = machine.total_vram_gb > 0 ? ((machine.total_vram_gb - machine.free_vram_gb) / machine.total_vram_gb) * 100 : 0;
  const ramTotal = machine.total_ram_gb ?? machine.free_ram_gb;
  const ramUsed = ramTotal > 0 ? ((ramTotal - machine.free_ram_gb) / ramTotal) * 100 : 0;
  const diskTotal = machine.total_disk_gb ?? machine.free_disk_gb;
  const diskUsed = diskTotal > 0 ? ((diskTotal - machine.free_disk_gb) / diskTotal) * 100 : 0;
  const status = machine.available_now ? 'Available now' : 'Busy';
  return (
    <div className="machine-card" data-testid={`machine-card-${machine.machine_key}`}>
      <div className="machine-card-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <div className="machine-icon"><Server size={20} /></div>
          <div>
            <div className="machine-hostname">{machine.display_name}</div>
            <div className="machine-ip">{machine.gpu_model ?? 'GPU'} · {machine.gpu_count} GPU{machine.gpu_count === 1 ? '' : 's'}</div>
          </div>
        </div>
        <span className={`badge ${machine.available_now ? 'badge-online' : 'badge-offline'}`}>
          <span className="status-dot" />
          {status}
        </span>
      </div>
      {!machine.available_now && (
        <div className="offline-notice" role="status">
          <Activity size={14} />
          {machine.availability_reason ?? 'Unavailable for interactive access'}
        </div>
      )}
      <div className="machine-specs">
        <span><Gpu size={14} /> {(machine.gpu_model ?? 'Any GPU')} x {machine.gpu_count}</span>
        <span><Activity size={14} /> {machine.workloads.length} active workload{machine.workloads.length === 1 ? '' : 's'}</span>
      </div>
      {machine.workloads.length > 0 && (
        <ul aria-label="Current work" style={{ margin: 0, paddingLeft: '1.1rem', fontSize: '0.82rem', color: 'var(--text-secondary)' }}>
          {machine.workloads.map((w, i) => (
            <li key={i}>
              {workloadLabel(w.kind)} · {w.state}{w.mine ? ' · Your work' : ''}
            </li>
          ))}
        </ul>
      )}
      <div className="resource-list">
        <ResourceBar label="VRAM" icon={<MemoryStick size={14} />} usedPercent={vramUsed} details={`${machine.free_vram_gb.toFixed(0)} / ${machine.total_vram_gb.toFixed(0)} GB free`} />
        <ResourceBar label="GPU Load" icon={<Gpu size={14} />} usedPercent={machine.gpu_load_percent ?? 0} details={`${(machine.gpu_load_percent ?? 0).toFixed(0)}%`} />
        <ResourceBar label="CPU Cores" icon={<Cpu size={14} />} usedPercent={machine.cpu_load_percent ?? 0} details={`${machine.cpu_cores} cores`} />
        <ResourceBar label="RAM" icon={<MemoryStick size={14} />} usedPercent={ramUsed} details={`${machine.free_ram_gb.toFixed(0)} / ${ramTotal.toFixed(0)} GB free`} />
        <ResourceBar label="Disk" icon={<HardDrive size={14} />} usedPercent={diskUsed} details={`${machine.free_disk_gb.toFixed(0)} GB free`} />
      </div>
    </div>
  );
};

export const MachineGrid: React.FC<{ machines: CapacityMachine[] }> = ({ machines }) => {
  if (machines.length === 0) {
    return (
      <div className="card" style={{ gridColumn: '1 / -1', textAlign: 'center', padding: '2rem 1rem' }}>
        <p className="error-text" style={{ margin: 0 }}>No online machines match these minimum requirements.</p>
      </div>
    );
  }
  return (
    <div className="machine-grid">
      {machines.map((m) => (
        <MachineCard key={m.machine_key} machine={m} />
      ))}
    </div>
  );
};
