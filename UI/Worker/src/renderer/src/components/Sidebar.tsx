import React from 'react'
import { Cpu, LayoutDashboard, Wifi, WifiOff } from 'lucide-react'

export type View = 'dashboard'

interface SidebarProps {
  view: View
  connected: boolean
  platform: string
}

const NAV_ITEMS: { key: View; label: string; icon: typeof LayoutDashboard }[] = [
  { key: 'dashboard', label: 'Dashboard', icon: LayoutDashboard }
]

const Sidebar: React.FC<SidebarProps> = ({ view, connected, platform }) => (
  <aside className="sidebar">
    <div className="sidebar-header">
      <div className="logo-mark">
        <Cpu size={20} />
      </div>
      <span>
        Worker <span className="logo-accent">Agent</span>
      </span>
    </div>

    <div className="nav-section-label">Monitoring</div>
    <nav className="sidebar-nav">
      {NAV_ITEMS.map(({ key, label, icon: Icon }) => (
        <button
          key={key}
          className={`nav-item${view === key ? ' active' : ''}`}
        >
          <Icon size={17} />
          {label}
        </button>
      ))}
    </nav>

    <div className="sidebar-footer">
      <div className="sidebar-status">
        <span className={`status-dot${connected ? ' status-dot-on' : ''}`} />
        <span>{connected ? 'Connected to scheduler' : 'Disconnected'}</span>
      </div>
      <div className="sidebar-meta">
        {connected ? <Wifi size={13} /> : <WifiOff size={13} />}
        <span className="mono">
          {platform} · electron {window.worker?.versions.electron ?? '0.1.0'}
        </span>
      </div>
    </div>
  </aside>
)

export default Sidebar
