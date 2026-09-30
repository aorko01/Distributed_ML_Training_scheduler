import React from 'react'
import { Cpu, History, LayoutDashboard, ScrollText, Wifi, WifiOff } from 'lucide-react'

export type View = 'dashboard' | 'past-jobs' | 'worker-logs'

interface SidebarProps {
  view: View
  onViewChange: (view: View) => void
  counts: {
    running: number
    past: number
    logs: number
  }
  connected: boolean
  apiReachable: boolean
  platform: string
}

const NAV_ITEMS: { key: View; label: string; icon: typeof LayoutDashboard; count: keyof SidebarProps['counts'] }[] = [
  { key: 'dashboard', label: 'Dashboard', icon: LayoutDashboard, count: 'running' },
  { key: 'past-jobs', label: 'Past jobs', icon: History, count: 'past' },
  { key: 'worker-logs', label: 'Worker logs', icon: ScrollText, count: 'logs' }
]

const Sidebar: React.FC<SidebarProps> = ({
  view,
  onViewChange,
  counts,
  connected,
  apiReachable,
  platform
}) => (
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
      {NAV_ITEMS.map(({ key, label, icon: Icon, count }) => (
        <button
          key={key}
          type="button"
          className={`nav-item${view === key ? ' active' : ''}`}
          aria-current={view === key ? 'page' : undefined}
          onClick={() => onViewChange(key)}
        >
          <Icon size={17} />
          <span>{label}</span>
          <span className="nav-count">{counts[count]}</span>
        </button>
      ))}
    </nav>

    <div className="sidebar-footer">
      <div className="sidebar-status">
        <span className={`status-dot${connected ? ' status-dot-on' : ''}`} />
        <span>
          {!apiReachable ? 'Worker service offline' : connected ? 'Scheduler connected' : 'Scheduler offline'}
        </span>
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
