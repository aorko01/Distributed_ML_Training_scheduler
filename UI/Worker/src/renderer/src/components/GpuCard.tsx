import React from 'react'
import { Check, MonitorCog, X } from 'lucide-react'
import type { GpuInfo } from '../types'

interface GpuCardProps {
  gpu: GpuInfo
  connected: boolean
  cuda: boolean
}

const GpuCard: React.FC<GpuCardProps> = ({ gpu, connected, cuda }) => {
  const vramPct = gpu.vramTotalGb > 0 ? Math.round((gpu.vramUsedGb / gpu.vramTotalGb) * 100) : 0
  const vramBar = connected ? vramPct : 0
  const loadBar = connected ? Math.round(gpu.load) : 0

  return (
    <div className="card gpu-card">
      <div className="card-header">
        <MonitorCog size={20} color="var(--status-running)" />
        <h3 style={{ margin: 0 }}>{gpu.name}</h3>
        <span className="badge badge-vram">{gpu.vramTotalGb} GB</span>
      </div>

      <div className="gpu-meta">
        <span>
          GPU {gpu.index} · CUDA {cuda ? <Check size={13} className="inline" /> : null}
        </span>
      </div>

      <div className="gpu-meter">
        <div className="gpu-meter-row">
          <span className="gpu-meter-label">GPU Load</span>
          <span className="gpu-meter-value">{connected ? `${Math.round(gpu.load)}%` : '—'}</span>
        </div>
        <div className="progress-bar-track">
          <div className="progress-bar-fill load" style={{ width: `${loadBar}%` }} />
        </div>
      </div>

      <div className="gpu-meter">
        <div className="gpu-meter-row">
          <span className="gpu-meter-label">VRAM Used</span>
          <span className="gpu-meter-value">
            {connected ? `${gpu.vramUsedGb} / ${gpu.vramTotalGb} GB` : `— / ${gpu.vramTotalGb} GB`}
          </span>
        </div>
        <div className="progress-bar-track">
          <div className="progress-bar-fill vram" style={{ width: `${vramBar}%` }} />
        </div>
      </div>

      <div className="gpu-meta gpu-meta-footer">
        <span>
          Free VRAM: <strong className="mono">{connected ? `${gpu.vramFreeGb} GB` : '—'}</strong>
        </span>
        <span>
          Temp: <strong className="mono">{connected && gpu.temperatureC > 0 ? `${gpu.temperatureC}°C` : '—'}</strong>
        </span>
      </div>

      {!connected ? (
        <div className="gpu-idle-note">
          <X size={14} /> Worker offline — no jobs accepted
        </div>
      ) : null}
    </div>
  )
}

export default GpuCard
