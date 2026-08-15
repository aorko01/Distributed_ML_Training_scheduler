import React from 'react'
import { Plug, PlugZap, Radio, RefreshCw } from 'lucide-react'

interface TopBarProps {
  connected: boolean
  hostname: string
  ipAddress: string
  lastHeartbeat: string
  onDisconnect: () => void
  onReconnect: () => void
}

const TopBar: React.FC<TopBarProps> = ({
  connected,
  hostname,
  ipAddress,
  lastHeartbeat,
  onDisconnect,
  onReconnect
}) => (
  <div className="top-header">
    <div className="top-header-left">
      <h2 className="page-title">{hostname}</h2>
      <span className="top-header-sub mono">
        {ipAddress} · heartbeat {lastHeartbeat}
      </span>
    </div>

    <div className="top-header-actions">
      <span className={`badge ${connected ? 'badge-online' : 'badge-offline'}`}>
        {connected ? <Radio size={12} /> : <PlugZap size={12} />}
        {connected ? 'Online' : 'Offline'}
      </span>

      {connected ? (
        <button className="btn btn-danger btn-sm" onClick={onDisconnect}>
          <Plug size={14} />
          Disconnect
        </button>
      ) : (
        <button className="btn btn-success btn-sm" onClick={onReconnect}>
          <RefreshCw size={14} />
          Reconnect
        </button>
      )}
    </div>
  </div>
)

export default TopBar
