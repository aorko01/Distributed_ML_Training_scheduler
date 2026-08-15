import React from 'react'

interface RingGaugeProps {
  value: number
  size?: number
  stroke?: number
  color?: string
  label?: string
}

const RingGauge: React.FC<RingGaugeProps> = ({
  value,
  size = 96,
  stroke = 8,
  color = 'var(--accent-primary)',
  label
}) => {
  const radius = (size - stroke) / 2
  const circumference = 2 * Math.PI * radius
  const clamped = Math.min(100, Math.max(0, value))
  const offset = circumference - (clamped / 100) * circumference

  return (
    <div className="ring-gauge" style={{ width: size, height: size }}>
      <svg width={size} height={size}>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--bg-tertiary)"
          strokeWidth={stroke}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
          style={{ transition: 'stroke-dashoffset 0.6s ease' }}
        />
      </svg>
      <div className="ring-gauge-center">
        <span className="ring-gauge-value">{Math.round(clamped)}%</span>
        {label ? <span className="ring-gauge-label">{label}</span> : null}
      </div>
    </div>
  )
}

export default RingGauge
