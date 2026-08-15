import React from 'react'
import {
  Activity,
  Cpu,
  Gauge,
  Globe,
  HardDrive,
  MemoryStick,
  PlugZap,
  Server,
  WifiOff
} from 'lucide-react'
import TopBar from '../components/TopBar'
import StatCard from '../components/StatCard'
import InfoCard from '../components/InfoCard'
import GpuCard from '../components/GpuCard'
import Sparkline from '../components/Sparkline'
import JobsTable from '../components/JobsTable'
import { useSimulation } from '../hooks/useSimulation'
import { mockJobs, mockWorker } from '../data/mock'

interface DashboardProps {
  connected: boolean
  onDisconnect: () => void
  onReconnect: () => void
}

const Dashboard: React.FC<DashboardProps> = ({ connected, onDisconnect, onReconnect }) => {
  const metrics = useSimulation(connected)

  const vramPct =
    mockWorker.gpuVramTotalGb > 0
      ? Math.round((metrics.vramUsedGb / mockWorker.gpuVramTotalGb) * 100)
      : 0

  return (
    <div className="dashboard">
      <TopBar
        connected={connected}
        hostname={mockWorker.hostname}
        ipAddress={mockWorker.ipAddress}
        lastHeartbeat={metrics.lastHeartbeat}
        onDisconnect={onDisconnect}
        onReconnect={onReconnect}
      />

      <div className="page-content fade-in">
        {!connected ? (
          <div className="banner banner-warn">
            <WifiOff size={16} />
            Worker is disconnected from the scheduler. No heartbeats or job polling are running.
          </div>
        ) : null}

        <div className="metrics-grid">
          <StatCard
            title="CPU Load"
            value={connected ? metrics.cpuLoad.toFixed(1) : '—'}
            unit="%"
            sub={`${mockWorker.cpus} cores`}
            percent={connected ? metrics.cpuLoad : 0}
            icon={Cpu}
          />
          <StatCard
            title="Memory"
            value={connected ? metrics.memUsage.toFixed(1) : '—'}
            unit="%"
            sub={`${mockWorker.memTotalGb} GB total`}
            percent={connected ? metrics.memUsage : 0}
            color="#a855f7"
            icon={MemoryStick}
          />
          <StatCard
            title="GPU Load"
            value={connected ? Math.round(metrics.gpuLoad) : '—'}
            unit="%"
            sub={mockWorker.gpuName}
            percent={connected ? metrics.gpuLoad : 0}
            color="#22c55e"
            icon={Gauge}
          />
          <StatCard
            title="VRAM Used"
            value={connected ? metrics.vramUsedGb : '—'}
            unit={`/${mockWorker.gpuVramTotalGb} GB`}
            sub={`${connected ? metrics.vramFreeGb : '—'} GB free`}
            percent={connected ? vramPct : 0}
            color="#eab308"
            icon={HardDrive}
          />
        </div>

        <div className="grid-2">
          <div className="stack">
            <InfoCard
              title="Worker Information"
              icon={<Server size={20} color="var(--text-secondary)" />}
              rows={[
                { label: 'Worker ID', value: mockWorker.workerId, mono: true },
                { label: 'Hostname', value: mockWorker.hostname, mono: true },
                { label: 'IP Address', value: mockWorker.ipAddress, mono: true },
                { label: 'Operating System', value: mockWorker.os },
                { label: 'Platform', value: `${mockWorker.platform} (${mockWorker.arch})` },
                { label: 'Container Runtime', value: mockWorker.dockerAvailable ? 'Docker available' : 'Unavailable' }
              ]}
            />
            <InfoCard
              title="Scheduler Connection"
              icon={<Globe size={20} color="var(--text-secondary)" />}
              rows={[
                { label: 'Scheduler URL', value: mockWorker.schedulerUrl, mono: true },
                { label: 'Heartbeat Interval', value: `${mockWorker.heartbeatIntervalSec}s` },
                { label: 'Job Poll Interval', value: `${mockWorker.jobPollIntervalSec}s` },
                {
                  label: 'Status',
                  value: connected ? 'Connected' : 'Disconnected',
                  color: connected ? 'var(--status-online)' : 'var(--status-offline)'
                }
              ]}
            />
          </div>

          <GpuCard
            info={mockWorker}
            gpuLoad={metrics.gpuLoad}
            vramUsedGb={metrics.vramUsedGb}
            vramFreeGb={metrics.vramFreeGb}
            gpuTempC={metrics.gpuTempC}
            connected={connected}
          />
        </div>

        <div className="grid-2 grid-2-wide">
          <div className="card chart-card">
            <div className="card-header">
              <Activity size={20} color="var(--accent-primary)" />
              <h3 style={{ margin: 0 }}>CPU Utilization</h3>
              <span className="badge badge-online" style={{ marginLeft: 'auto' }}>
                live
              </span>
            </div>
            <div className="sparkline-wrap">
              <Sparkline data={connected ? metrics.cpuHistory : metrics.cpuHistory.map(() => 0)} />
              <div className="sparkline-labels">
                <span>0s</span>
                <span>{Math.round(metrics.cpuLoad)}% now</span>
              </div>
            </div>
          </div>

          <JobsTable jobs={mockJobs} connected={connected} />
        </div>

        <div className="dummy-note">
          <PlugZap size={13} />
          Dummy UI — all data is simulated locally. No connection to the worker process yet.
        </div>
      </div>
    </div>
  )
}

export default Dashboard
