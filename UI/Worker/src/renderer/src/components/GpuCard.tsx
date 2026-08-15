import React from 'react'
import { Check, MonitorCog, X } from 'lucide-react'
import type { WorkerInfo } from '../data/mock'

interface GpuCardProps {
  info: WorkerInfo
  gpuLoad: number
  vramUsedGb: number
  vramFreeGb: number
  gpuTempC: number
  connected: boolean
}

const GpuCard: React.FC<GpuCardProps> = ({ info, gpuLoad, vramUsedGb, vramFreeGb, gpuTempC, connected }) => {
  const vramTotal = info.gpuVramTotalGb
  const vramPct = vramTotal > 0 ? Math.round((vramUsedGb / vramTotal) * 100) : 0
  const vramBar = connected ? vramPct : 0
  const loadBar = connected ? Math.round(gpuLoad) : 0

  return (
    <div className="card gpu-card">
      <div className="card-header">
        <MonitorCog size={20} color="var(--status-running)" />
        <h3 style={{ margin: 0 }}>{info.gpuName}</h3>
        <span className="badge badge-vram">24 GB</span>
      </div>

      <div className="gpu-meta">
        <span>
          {info.gpuCount} GPU{info.gpuCount > 1 ? 's' : ''} · CUDA{' '}
          {info.cudaAvailable ? <Check size={13} className="inline" /> : null}
        </span>
      </div>

      <div className="gpu-meter">
        <div className="gpu-meter-row">
          <span className="gpu-meter-label">GPU Load</span>
          <span className="gpu-meter-value">{connected ? `${Math.round(gpuLoad)}%` : '—'}</span>
        </div>
        <div className="progress-bar-track">
          <div
            className="progress-bar-fill load"
            style={{ width: `${loadBar}%` }}
          />
        </div>
      </div>

      <div className="gpu-meter">
        <div className="gpu-meter-row">
          <span className="gpu-meter-label">VRAM Used</span>
          <span className="gpu-meter-value">
            {connected ? `${vramUsedGb} / ${vramTotal} GB` : `— / ${vramTotal} GB`}
          </span>
        </div>
        <div className="progress-bar-track">
          <div className="progress-bar-fill vram" style={{ width: `${vramBar}%` }} />
        </div>
      </div>

      <div className="gpu-meta gpu-meta-footer">
        <span>
          Free VRAM: <strong className="mono">{connected ? `${vramFreeGb} GB` : '—'}</strong>
        </span>
        <span>
          Temp: <strong className="mono">{connected ? `${gpuTempC}°C` : '—'}</strong>
        </span>
      </div>

      {!connected ? (
        <div className="gpu-idle-note">
          <X size={14} /> GPU idle — no jobs accepted
        </div>
      ) : null}
    </div>
  )
}

export default GpuCard
