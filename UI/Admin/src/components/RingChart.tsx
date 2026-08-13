import React from 'react';

export interface RingSegment {
  label: string;
  value: number;
  color: string;
}

interface RingChartProps {
  segments: RingSegment[];
  size?: number;
  strokeWidth?: number;
  centerLabel?: string;
  centerSub?: string;
}

const RingChart: React.FC<RingChartProps> = ({
  segments,
  size = 180,
  strokeWidth = 22,
  centerLabel,
  centerSub,
}) => {
  const total = segments.reduce((sum, s) => sum + s.value, 0);
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const center = size / 2;

  let cumulative = 0;

  return (
    <div style={{ position: 'relative', width: size, height: size, flexShrink: 0 }}>
      <svg width={size} height={size}>
        <circle
          cx={center}
          cy={center}
          r={radius}
          fill="none"
          stroke="var(--bg-tertiary)"
          strokeWidth={strokeWidth}
        />
        {segments.map((seg) => {
          const fraction = total > 0 ? seg.value / total : 0;
          const dashLength = fraction * circumference;
          const dashOffset = -cumulative * circumference;
          cumulative += fraction;
          return (
            <circle
              key={seg.label}
              cx={center}
              cy={center}
              r={radius}
              fill="none"
              stroke={seg.color}
              strokeWidth={strokeWidth}
              strokeLinecap="butt"
              strokeDasharray={`${dashLength} ${circumference - dashLength}`}
              strokeDashoffset={dashOffset}
              transform={`rotate(-90 ${center} ${center})`}
            />
          );
        })}
      </svg>
      <div
        style={{
          position: 'absolute',
          inset: 0,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          pointerEvents: 'none',
        }}
      >
        {centerLabel && (
          <div style={{ fontSize: '1.75rem', fontWeight: 700 }}>{centerLabel}</div>
        )}
        {centerSub && (
          <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>{centerSub}</div>
        )}
      </div>
    </div>
  );
};

export default RingChart;
