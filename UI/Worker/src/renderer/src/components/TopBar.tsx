import React from 'react'
import { Ban, CheckCircle2, PlugZap, Radio } from 'lucide-react'

interface TopBarProps {
  connected: boolean
  acceptingJobs: boolean
  hostname: string
  ipAddress: string
  lastHeartbeat: string
  onToggleAccepting: () => void
}

const TopBar: React.FC<TopBarProps> = ({
  connected,
  acceptingJobs,
  hostname,
  ipAddress,
  lastHeartbeat,
  onToggleAccepting
}) => {
  const badgeClass = connected ? 'badge-online' : 'badge-offline'
  const badgeLabel = connected ? 'Online' : 'Offline'
  const BadgeIcon = connected ? Radio : PlugZap

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

        {acceptingJobs ? (
          <button className="btn btn-danger btn-sm" onClick={onToggleAccepting}>
            <Ban size={14} />
            Accept no more jobs
          </button>
        ) : (
          <button className="btn btn-success btn-sm" onClick={onToggleAccepting}>
            <CheckCircle2 size={14} />
            Accept jobs
          </button>
        )}
      </div>
    </div>
  )
}

export default TopBar
