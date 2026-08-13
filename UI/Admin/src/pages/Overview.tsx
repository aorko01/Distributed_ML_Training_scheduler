import React, { useState } from 'react';
import { Server, Gauge, ListOrdered, Cpu, TrendingUp } from 'lucide-react';
import RingChart from '../components/RingChart';
import BarChart from '../components/BarChart';
import {
  clusterOverview,
  throughput,
  type ThroughputPeriod,
  type ThroughputPoint,
} from '../data/mock';

const PERIODS: { value: ThroughputPeriod; label: string }[] = [
  { value: 'daily', label: 'Daily' },
  { value: 'weekly', label: 'Weekly' },
  { value: 'monthly', label: 'Monthly' },
  { value: 'yearly', label: 'Yearly' },
];

const Overview: React.FC = () => {
  const [period, setPeriod] = useState<ThroughputPeriod>('weekly');
  const [throughputData, setThroughputData] = useState<ThroughputPoint[]>(throughput[period]);
  const [selectedPoint, setSelectedPoint] = useState<number | null>(null);

  const handlePeriodChange = (next: ThroughputPeriod) => {
    setPeriod(next);
    setThroughputData(throughput[next]);
    setSelectedPoint(null);
  };

  const { nodesOnline, nodesTotal, clusterLoad, queueDepth, gpusAllocated, gpusTotal, distribution } =
    clusterOverview;

  const distributionSegments = [
    { label: 'Batch Training', value: distribution.batch, color: 'var(--batch-color)' },
    { label: 'Experimentation', value: distribution.experimentation, color: 'var(--experiment-color)' },
    { label: 'Idle', value: distribution.idle, color: 'var(--idle-color)' },
  ];

  const totalDistribution = distribution.batch + distribution.experimentation + distribution.idle;

  const selectedDatum =
    selectedPoint !== null ? throughputData[selectedPoint] : throughputData[throughputData.length - 1];

  return (
    <div className="fade-in">
      <h1>Cluster Overview</h1>

      <div className="metrics-grid">
        <div className="metric-card">
          <div className="metric-card-header">
            <span className="metric-title">Nodes Online</span>
            <Server size={20} color="var(--text-secondary)" />
          </div>
          <div className="metric-value">
            {nodesOnline}
            <span style={{ fontSize: '1rem', color: 'var(--text-secondary)', fontWeight: 500 }}>
              {' '}
              / {nodesTotal}
            </span>
          </div>
          <div className="metric-sub">{nodesTotal - nodesOnline} offline or draining</div>
        </div>

        <div className="metric-card">
          <div className="metric-card-header">
            <span className="metric-title">Cluster Load</span>
            <Gauge size={20} color="var(--text-secondary)" />
          </div>
          <div className="metric-value">{clusterLoad}%</div>
          <div className="metric-sub">
            <div className="progress-bar-track" style={{ marginTop: '0.5rem' }}>
              <div className="progress-bar-fill load" style={{ width: `${clusterLoad}%` }} />
            </div>
          </div>
        </div>

        <div className="metric-card">
          <div className="metric-card-header">
            <span className="metric-title">Queue Depth</span>
            <ListOrdered size={20} color="var(--text-secondary)" />
          </div>
          <div className="metric-value">{queueDepth}</div>
          <div className="metric-sub">jobs awaiting scheduling</div>
        </div>

        <div className="metric-card">
          <div className="metric-card-header">
            <span className="metric-title">GPU Allocation</span>
            <Cpu size={20} color="var(--text-secondary)" />
          </div>
          <div className="metric-value">
            {gpusAllocated}
            <span style={{ fontSize: '1rem', color: 'var(--text-secondary)', fontWeight: 500 }}>
              {' '}
              / {gpusTotal}
            </span>
          </div>
          <div className="metric-sub">
            <div className="progress-bar-track" style={{ marginTop: '0.5rem' }}>
              <div
                className="progress-bar-fill mem"
                style={{ width: `${Math.round((gpusAllocated / gpusTotal) * 100)}%` }}
              />
            </div>
          </div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.6fr', gap: '1.5rem' }}>
        <div className="card chart-card">
          <div className="chart-card-header">
            <h3 style={{ margin: 0 }}>Resource Distribution</h3>
          </div>
          <div className="ring-container">
            <RingChart
              segments={distributionSegments}
              centerLabel={`${gpusAllocated}`}
              centerSub="GPUs busy"
            />
            <div className="ring-legend">
              {distributionSegments.map((seg) => (
                <div key={seg.label} className="legend-item">
                  <span className="legend-dot" style={{ backgroundColor: seg.color }} />
                  <span>{seg.label}</span>
                  <span className="legend-value">{seg.value}%</span>
                  <span className="legend-pct">
                    ({Math.round((seg.value / totalDistribution) * 100)})
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="card chart-card">
          <div className="chart-card-header">
            <h3 style={{ margin: 0 }}>Job Throughput</h3>
            <div className="segmented">
              {PERIODS.map((p) => (
                <button
                  key={p.value}
                  className={period === p.value ? 'active' : ''}
                  onClick={() => handlePeriodChange(p.value)}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>
          <BarChart
            data={throughputData}
            onSelect={(i) => setSelectedPoint(i)}
          />
          <div
            style={{
              marginTop: '1rem',
              paddingTop: '1rem',
              borderTop: '1px solid var(--border-color)',
              display: 'flex',
              alignItems: 'center',
              gap: '0.75rem',
              fontSize: '0.875rem',
              color: 'var(--text-secondary)',
            }}
          >
            <TrendingUp size={16} color="var(--accent-primary)" />
            {selectedDatum ? (
              <span>
                <strong style={{ color: 'var(--text-primary)' }}>{selectedDatum.jobs} jobs</strong> completed
                during <strong style={{ color: 'var(--text-primary)' }}>{selectedDatum.label}</strong>
              </span>
            ) : (
              <span>Select a bar to see details.</span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default Overview;
