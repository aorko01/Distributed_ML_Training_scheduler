import React from 'react'

interface SparklineProps {
  data: number[]
  width?: number
  height?: number
  color?: string
}

const Sparkline: React.FC<SparklineProps> = ({ data, width = 320, height = 72, color = 'var(--accent-primary)' }) => {
  const min = Math.min(...data, 0)
  const max = Math.max(...data, 1)
  const range = max - min || 1
  const stepX = width / Math.max(data.length - 1, 1)

  const points = data.map((value, i) => {
    const x = i * stepX
    const y = height - ((value - min) / range) * height
    return `${x},${y}`
  })

  const polyline = points.join(' ')
  const areaPath = `0,${height} ${polyline} ${width},${height}`

  return (
    <svg className="sparkline" width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
      <polygon points={areaPath} fill={color} opacity="0.12" />
      <polyline points={polyline} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}

export default Sparkline
