import React, { useEffect, useState } from 'react';
import { Server, Gauge, ListOrdered, Cpu, TrendingUp } from 'lucide-react';
import BarChart from '../components/BarChart';
import {
  clusterOverview as mockOverview,
  throughput as mockThroughput,
  type ThroughputPeriod,
  type ThroughputPoint,
} from '../data/mock';
import {
  fetchOverview,
  fetchThroughput,
  type OverviewStats,
} from '../services/api';

const PERIODS: { value: ThroughputPeriod; label: string }[] = [
  { value: 'daily', label: 'Daily' },
  { value: 'weekly', label: 'Weekly' },
  { value: 'monthly', label: 'Monthly' },
  { value: 'yearly', label: 'Yearly' },
];

const REFRESH_INTERVAL_MS = 5000;

const Overview: React.FC = () => {
  const [period, setPeriod] = useState<ThroughputPeriod>('weekly');
  const [stats, setStats] = useState<OverviewStats>({
    nodes_online: mockOverview.nodesOnline,
    nodes_total: mockOverview.nodesTotal,
    cluster_load: mockOverview.clusterLoad,
    queue_depth: mockOverview.queueDepth,
    gpus_allocated: mockOverview.gpusAllocated,
    gpus_total: mockOverview.gpusTotal,
  });
  const [throughputData, setThroughputData] = useState<ThroughputPoint[]>(mockThroughput[period]);
  const [selectedPoint, setSelectedPoint] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;

    const refresh = async () => {
      try {
        const [overview, throughput] = await Promise.all([fetchOverview(), fetchThroughput()]);
        if (cancelled) return;
        setStats(overview);
        setThroughputData(throughput[period]);
      } catch (err) {
        if (!cancelled) {
          console.error('Failed to load cluster overview:', err);
        }
      }
    };

    void refresh();
    const interval = window.setInterval(refresh, REFRESH_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [period]);

  const handlePeriodChange = (next: ThroughputPeriod) => {
    setPeriod(next);
    setSelectedPoint(null);
  };

  const { nodesOnline, nodesTotal, clusterLoad, queueDepth, gpusAllocated, gpusTotal } = {
    nodesOnline: stats.nodes_online,
    nodesTotal: stats.nodes_total,
    clusterLoad: stats.cluster_load,
    queueDepth: stats.queue_depth,
    gpusAllocated: stats.gpus_allocated,
    gpusTotal: stats.gpus_total,
  };

  const gpuUsagePercent = gpusTotal > 0 ? Math.round((gpusAllocated / gpusTotal) * 100) : 0;

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
          <div className="metric-sub">{nodesTotal - nodesOnline} offline</div>
        </div>

        <div className="metric-card">
          <div className="metric-card-header">
            <span className="metric-title">Cluster Load</span>
            <Gauge size={20} color="var(--text-secondary)" />
          </div>
          <div className="metric-value">{Math.round(clusterLoad)}%</div>
          <div className="metric-sub">
            <div className="progress-bar-track" style={{ marginTop: '0.5rem' }}>
              <div className="progress-bar-fill load" style={{ width: `${Math.min(100, clusterLoad)}%` }} />
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
              <div className="progress-bar-fill mem" style={{ width: `${gpuUsagePercent}%` }} />
            </div>
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
  );
};

export default Overview;