import React from 'react'

interface InfoRow {
  label: string
  value: string
  mono?: boolean
  color?: string
}

interface InfoCardProps {
  title: string
  icon?: React.ReactNode
  rows: InfoRow[]
}

const InfoCard: React.FC<InfoCardProps> = ({ title, icon, rows }) => (
  <div className="card">
    <div className="card-header">
      {icon}
      <h3 style={{ margin: 0 }}>{title}</h3>
    </div>
    <div className="info-list">
      {rows.map((row) => (
        <div key={row.label} className="info-row">
          <span className="info-label">{row.label}</span>
          <span
            className={`info-value${row.mono ? ' mono' : ''}`}
            style={row.color ? { color: row.color } : undefined}
          >
            {row.value}
          </span>
        </div>
      ))}
    </div>
  </div>
)

export default InfoCard
