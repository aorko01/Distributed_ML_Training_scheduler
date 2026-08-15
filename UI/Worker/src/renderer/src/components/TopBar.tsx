import React from 'react'
import { Pause, Play, PlugZap, Radio, Settings } from 'lucide-react'

interface TopBarProps {
  connected: boolean
  paused: boolean
  hostname: string
  ipAddress: string
  lastHeartbeat: string
  onTogglePause: () => void
  onOpenConfig: () => void
}

const TopBar: React.FC<TopBarProps> = ({
  connected,
  paused,
  hostname,
  ipAddress,
  lastHeartbeat,
  onTogglePause,
  onOpenConfig
}) => {
  const badgeClass = paused ? 'badge-offline' : connected ? 'badge-online' : 'badge-offline'
  const badgeLabel = paused ? 'Paused' : connected ? 'Online' : 'Offline'
  const BadgeIcon = paused ? Pause : connected ? Radio : PlugZap

  return (
    <div className="top-header">
      <div className="top-header-left">
        <h2 className="page-title">{hostname}</h2>
        <span className="top-header-sub mono">
          {ipAddress} · heartbeat {lastHeartbeat}
        </span>
      </div>

      <div className="top-header-actions">
        <span className={`badge ${badgeClass}`}>
          <BadgeIcon size={12} />
          {badgeLabel}
        </span>

        <button className="btn btn-secondary btn-sm" onClick={onOpenConfig}>
          <Settings size={14} />
          Settings
        </button>

        {paused ? (
          <button className="btn btn-success btn-sm" onClick={onTogglePause}>
            <Play size={14} />
            Resume
          </button>
        ) : (
          <button className="btn btn-danger btn-sm" onClick={onTogglePause}>
            <Pause size={14} />
            Pause
          </button>
        )}
      </div>
    </div>
  )
}

export default TopBar
