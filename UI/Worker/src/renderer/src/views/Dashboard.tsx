import React from 'react'
import {
  Activity,
  Cpu,
  Database,
  Gauge,
  Globe,
  HardDrive,
  MemoryStick,
  Network,
  Pause,
  Server
} from 'lucide-react'
import TopBar from '../components/TopBar'
import StatCard from '../components/StatCard'
import InfoCard from '../components/InfoCard'
import GpuCard from '../components/GpuCard'
import Sparkline from '../components/Sparkline'
import JobsTable from '../components/JobsTable'
import { formatBytes, type EventRecord } from '../types'
import type { WorkerData } from '../hooks/useWorkerData'

interface DashboardProps extends WorkerData {
  onOpenConfig: () => void
}

const LEVEL_CLASS: Record<EventRecord['level'], string> = {
  info: 'log-info',
  warn: 'log-warn',
  error: 'log-error',
  success: 'log-success'
}

const Dashboard: React.FC<DashboardProps> = ({
  worker,
  metrics,
  gpus,
  jobs,
  events,
  connected,
  paused,
  apiReachable,
  cpuHistory,
  gpuHistory,
  diskReadHistory,
  netRecvHistory,
  lastHeartbeat,
  error,
  pauseWorker,
  resumeWorker,
  onOpenConfig
}) => {
  const cpuLoad = metrics?.cpuLoad ?? 0
  const memUsage = metrics?.memUsage ?? 0
  const gpuLoad = metrics?.gpuLoad ?? 0
  const vramUsedGb = metrics?.vramUsedGb ?? 0
  const vramFreeGb = metrics?.vramFreeGb ?? 0
  const vramTotalGb = metrics?.vramTotalGb ?? worker?.gpuVramTotalGb ?? 0
  const memTotalGb = metrics?.memTotalGb ?? worker?.memTotalGb ?? 0
  const cpus = worker?.cpus ?? 0
  const gpuName = worker?.gpuName ?? 'No GPU detected'
  const cuda = worker?.cudaAvailable ?? false
  const history = connected ? cpuHistory : cpuHistory.map(() => 0)
  const gpuHist = connected ? gpuHistory : gpuHistory.map(() => 0)
  const diskHist = connected ? diskReadHistory : diskReadHistory.map(() => 0)
  const netHist = connected ? netRecvHistory : netRecvHistory.map(() => 0)

  const vramPct = vramTotalGb > 0 ? Math.round((vramUsedGb / vramTotalGb) * 100) : 0
  const diskNow = metrics?.diskReadBytesPerS ?? 0
  const diskWrite = metrics?.diskWriteBytesPerS ?? 0
  const netNow = metrics?.netRecvBytesPerS ?? 0
  const netSent = metrics?.netSentBytesPerS ?? 0

  const recentEvents = events.slice(-20).reverse()

  return (
    <div className="dashboard">
      <TopBar
        connected={connected}
        paused={paused}
        hostname={worker?.hostname ?? 'Worker Agent'}
        ipAddress={worker?.ipAddress ?? '—'}
        lastHeartbeat={lastHeartbeat}
        onTogglePause={paused ? resumeWorker : pauseWorker}
        onOpenConfig={onOpenConfig}
      />

      <div className="page-content fade-in">
        {!connected ? (
          <div className="banner banner-warn">
            {paused ? (
              <>
                <Pause size={16} />
                Worker is paused. Heartbeats and job polling are stopped — resume to continue.
              </>
            ) : !apiReachable ? (
              <>
                <Globe size={16} />
                Worker API unreachable{error ? ` — ${error}` : ''}. Is the worker agent running?
              </>
            ) : (
              <>
                <Globe size={16} />
                Worker is disconnected from the scheduler. No heartbeats or job polling are running.
              </>
            )}
          </div>
        ) : null}

        <div className="metrics-grid">
          <StatCard
            title="CPU Load"
            value={connected ? cpuLoad.toFixed(1) : '—'}
            unit="%"
            sub={`${cpus} cores`}
            percent={connected ? cpuLoad : 0}
            icon={Cpu}
          />
          <StatCard
            title="Memory"
            value={connected ? memUsage.toFixed(1) : '—'}
            unit="%"
            sub={`${memTotalGb} GB total`}
            percent={connected ? memUsage : 0}
            color="#a855f7"
            icon={MemoryStick}
          />
          <StatCard
            title="GPU Load"
            value={connected ? Math.round(gpuLoad) : '—'}
            unit="%"
            sub={gpuName}
            percent={connected ? gpuLoad : 0}
            color="#22c55e"
            icon={Gauge}
          />
          <StatCard
            title="VRAM Used"
            value={connected ? vramUsedGb : '—'}
            unit={`/${vramTotalGb} GB`}
            sub={`${connected ? vramFreeGb : '—'} GB free`}
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
                { label: 'Worker ID', value: worker?.workerId ?? '—', mono: true },
                { label: 'Hostname', value: worker?.hostname ?? '—', mono: true },
                { label: 'IP Address', value: worker?.ipAddress ?? '—', mono: true },
                { label: 'Operating System', value: worker?.os ?? '—' },
                { label: 'Platform', value: worker ? `${worker.platform} (${worker.arch})` : '—' },
                { label: 'Container Runtime', value: worker?.dockerAvailable ? 'Docker available' : 'Unavailable' }
              ]}
            />
            <InfoCard
              title="Scheduler Connection"
              icon={<Globe size={20} color="var(--text-secondary)" />}
              rows={[
                { label: 'Scheduler URL', value: worker?.schedulerUrl ?? '—', mono: true },
                { label: 'Heartbeat Interval', value: worker ? `${worker.heartbeatIntervalSec}s` : '—' },
                { label: 'Job Poll Interval', value: worker ? `${worker.jobPollIntervalSec}s` : '—' },
                {
                  label: 'Status',
                  value: paused ? 'Paused' : connected ? 'Connected' : 'Disconnected',
                  color: paused ? 'var(--status-pending)' : connected ? 'var(--status-online)' : 'var(--status-offline)'
                }
              ]}
            />
          </div>

          <div className="stack">
            <div className="gpus-grid">
              {gpus.length > 0 ? (
                gpus.map((gpu) => <GpuCard key={gpu.index} gpu={gpu} connected={connected} cuda={cuda} />)
              ) : (
                <GpuCard
                  gpu={{
                    index: 0,
                    name: worker?.gpuName ?? 'No GPU detected',
                    load: gpuLoad,
                    vramUsedGb: vramUsedGb,
                    vramFreeGb: vramFreeGb,
                    vramTotalGb: vramTotalGb,
                    temperatureC: metrics?.gpuTempC ?? 0,
                    inUse: false
                  }}
                  connected={connected}
                  cuda={cuda}
                />
              )}
            </div>
          </div>
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
              <Sparkline data={history} />
              <div className="sparkline-labels">
                <span>0s</span>
                <span>{Math.round(cpuLoad)}% now</span>
              </div>
            </div>
          </div>

          <div className="card chart-card">
            <div className="card-header">
              <Gauge size={20} color="#22c55e" />
              <h3 style={{ margin: 0 }}>GPU Utilization</h3>
              <span className="badge badge-online" style={{ marginLeft: 'auto' }}>
                live
              </span>
            </div>
            <div className="sparkline-wrap">
              <Sparkline data={gpuHist} color="#22c55e" />
              <div className="sparkline-labels">
                <span>0s</span>
                <span>{Math.round(gpuLoad)}% now</span>
              </div>
            </div>
          </div>
        </div>

        <div className="grid-2 grid-2-wide">
          <div className="card chart-card">
            <div className="card-header">
              <Database size={20} color="#a855f7" />
              <h3 style={{ margin: 0 }}>Disk I/O</h3>
              <span className="badge badge-vram" style={{ marginLeft: 'auto' }}>
                read {formatBytes(diskNow)}
              </span>
            </div>
            <div className="sparkline-wrap">
              <Sparkline data={diskHist} color="#a855f7" />
              <div className="sparkline-labels">
                <span>0s</span>
                <span>write {formatBytes(diskWrite)}</span>
              </div>
            </div>
          </div>

          <div className="card chart-card">
            <div className="card-header">
              <Network size={20} color="#3b82f6" />
              <h3 style={{ margin: 0 }}>Network I/O</h3>
              <span className="badge badge-running" style={{ marginLeft: 'auto' }}>
                recv {formatBytes(netNow)}
              </span>
            </div>
            <div className="sparkline-wrap">
              <Sparkline data={netHist} color="#3b82f6" />
              <div className="sparkline-labels">
                <span>0s</span>
                <span>sent {formatBytes(netSent)}</span>
              </div>
            </div>
          </div>
        </div>

        <div className="grid-2 grid-2-wide">
          <div className="card event-feed-card">
            <div className="card-header">
              <Activity size={20} color="var(--text-secondary)" />
              <h3 style={{ margin: 0 }}>Recent Events</h3>
              <span className="badge badge-online" style={{ marginLeft: 'auto' }}>
                {events.length}
              </span>
            </div>
            <div className="event-feed">
              {recentEvents.length === 0 ? (
                <div className="empty-state" style={{ padding: '1.5rem' }}>
                  <span>No events yet.</span>
                </div>
              ) : (
                recentEvents.map((event, i) => (
                  <div key={`${event.time}-${i}`} className={`log-line ${LEVEL_CLASS[event.level]}`}>
                    <span className="log-time">[{event.time}]</span>
                    <span className="log-message">{event.message}</span>
                  </div>
                ))
              )}
            </div>
          </div>

          <JobsTable jobs={jobs} connected={connected} />
        </div>
      </div>
    </div>
  )
}

export default Dashboard
