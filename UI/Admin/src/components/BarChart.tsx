import React from 'react';

export interface BarDatum {
  label: string;
  jobs: number;
}

interface BarChartProps {
  data: BarDatum[];
  height?: number;
  onSelect?: (index: number) => void;
}

const BarChart: React.FC<BarChartProps> = ({ data, height = 200, onSelect }) => {
  const max = Math.max(...data.map((d) => d.jobs), 1);

  return (
    <div className="bar-chart" style={{ height }}>
      {data.map((point, i) => {
        const barHeight = Math.max(4, Math.round((point.jobs / max) * (height - 56)));
        return (
          <div
            key={point.label}
            className="bar-col"
            onClick={onSelect ? () => onSelect(i) : undefined}
            style={onSelect ? { cursor: 'pointer' } : undefined}
            title={`${point.label}: ${point.jobs} jobs`}
          >
            <div className="bar-value">{point.jobs}</div>
            <div className="bar-track">
              <div className="bar" style={{ height: barHeight }} />
            </div>
            <div className="bar-label">{point.label}</div>
          </div>
        );
      })}
    </div>
  );
};

export default BarChart;
