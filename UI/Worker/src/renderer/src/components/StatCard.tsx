import React from 'react'
import type { LucideIcon } from 'lucide-react'

interface StatCardProps {
  title: string
  value: string | number
  unit?: string
  sub?: string
  percent?: number
  color?: string
  icon: LucideIcon
}

const StatCard: React.FC<StatCardProps> = ({
  title,
  value,
  unit,
  sub,
  percent,
  color = 'var(--accent-primary)',
  icon: Icon
}) => {
  const pct = percent !== undefined ? Math.min(100, Math.max(0, percent)) : 0

  return (
    <div className="stat-card">
      <div className="stat-card-header">
        <span className="stat-card-title">{title}</span>
        <Icon size={18} color="var(--text-secondary)" />
      </div>
      <div className="stat-card-value">
        {value}
        {unit ? <span className="stat-card-unit">{unit}</span> : null}
      </div>
      {sub ? <div className="stat-card-sub">{sub}</div> : null}
      {percent !== undefined ? (
        <div className="progress-bar-track" style={{ marginTop: '0.75rem' }}>
          <div className="progress-bar-fill" style={{ width: `${pct}%`, backgroundColor: color }} />
        </div>
      ) : null}
    </div>
  )
}

export default StatCard
