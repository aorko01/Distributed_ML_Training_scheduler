import React from 'react'
import {
  Activity,
  Clock3,
  Cpu,
  Database,
  Gauge,
  Globe,
  HardDrive,
  Info,
  MemoryStick,
  Network,
  Server,
  ShieldCheck,
  TerminalSquare
} from 'lucide-react'
import TopBar from '../components/TopBar'
import StatCard from '../components/StatCard'
import InfoCard from '../components/InfoCard'
import GpuCard from '../components/GpuCard'
import Sparkline from '../components/Sparkline'
import JobsTable from '../components/JobsTable'
import { formatBytes, formatUptime } from '../types'
import type { WorkerData } from '../hooks/useWorkerData'

const Dashboard: React.FC<WorkerData> = ({
  worker,
  metrics,
  gpus,
  jobs,
  logs,
  status,
  connected,
  apiReachable,
  acceptingJobs,
  updatingAcceptingJobs,
  acceptingJobsError,
  cpuHistory,
  gpuHistory,
  diskReadHistory,
  netRecvHistory,
  lastHeartbeat,
  error,
  toggleAcceptingJobs
}) => {
  const cpuLoad = metrics?.cpuLoad ?? 0
  const memUsage = metrics?.memUsage ?? 0
  const gpuLoad = metrics?.gpuLoad ?? 0
  const vramUsedGb = metrics?.vramUsedGb ?? 0
  const vramFreeGb = metrics?.vramFreeGb ?? 0
  const vramTotalGb = metrics?.vramTotalGb ?? worker?.gpuVramTotalGb ?? 0
  const memTotalGb = metrics?.memTotalGb ?? worker?.memTotalGb ?? 0
  const diskTotalGb = metrics?.diskTotalGb ?? worker?.diskTotalGb ?? 0
  const diskFreeGb = metrics?.diskFreeGb ?? worker?.diskFreeGb ?? 0
  const cpus = worker?.cpus ?? 0
  const gpuName = worker?.gpuName ?? 'No GPU detected'
  const cuda = worker?.cudaAvailable ?? false
  const vramPct = vramTotalGb > 0 ? Math.round((vramUsedGb / vramTotalGb) * 100) : 0
  const diskUsedPct = diskTotalGb > 0 ? ((diskTotalGb - diskFreeGb) / diskTotalGb) * 100 : 0
  const live = apiReachable

  return (
    <div className="dashboard">
      <TopBar
        connected={connected}
        acceptingJobs={acceptingJobs}
        acceptingJobsDisabled={!apiReachable || updatingAcceptingJobs}
        hostname={worker?.hostname ?? 'DML Worker'}
        ipAddress={worker?.ipAddress ?? '—'}
        lastHeartbeat={lastHeartbeat}
        onToggleAccepting={toggleAcceptingJobs}
      />

      <div className="page-content fade-in">
        <section className="hero-strip">
          <div>
            <span className="eyebrow">LOCAL NODE OBSERVABILITY</span>
            <h1>Worker command center</h1>
            <p>Live hardware, scheduler assignments, and worker-service logs from this machine.</p>
          </div>
          <div className="hero-state">
            <span className={`hero-orb ${connected ? 'hero-orb-online' : ''}`} />
            <div>
              <strong>{connected ? 'Scheduler link healthy' : 'Scheduler link unavailable'}</strong>
              <span>{status?.mode ?? 'WAITING FOR WORKER'}</span>
            </div>
          </div>
        </section>

        {!apiReachable ? (
          <div className="banner banner-warn">
            <Globe size={16} />
            Worker service is unreachable{error ? ` — ${error}` : ''}. Check dml-worker.service.
          </div>
        ) : !connected ? (
          <div className="banner banner-warn">
            <Globe size={16} />
            The local worker is running, but its scheduler heartbeat is not healthy.
          </div>
        ) : null}

        {!acceptingJobs ? (
          <div className="banner banner-info">
            <Info size={16} />
            This worker is not accepting new jobs. Running jobs continue.
          </div>
        ) : null}

        {acceptingJobsError ? (
          <div className="banner banner-warn">
            <Info size={16} />
            Could not update job acceptance: {acceptingJobsError}
          </div>
        ) : null}

        <div className="metrics-grid">
          <StatCard
            title="CPU load"
            value={live ? cpuLoad.toFixed(1) : '—'}
            unit="%"
            sub={`${cpus} logical cores`}
            percent={live ? cpuLoad : 0}
            icon={Cpu}
          />
          <StatCard
            title="Memory"
            value={live ? memUsage.toFixed(1) : '—'}
            unit="%"
            sub={`${memTotalGb} GB installed`}
            percent={live ? memUsage : 0}
            color="#8b5cf6"
            icon={MemoryStick}
          />
          <StatCard
            title="GPU load"
            value={live ? Math.round(gpuLoad) : '—'}
            unit="%"
            sub={gpuName}
            percent={live ? gpuLoad : 0}
            color="#2dd4bf"
            icon={Gauge}
          />
          <StatCard
            title="VRAM used"
            value={live ? vramUsedGb : '—'}
            unit={`/${vramTotalGb} GB`}
            sub={`${live ? vramFreeGb : '—'} GB free`}
            percent={live ? vramPct : 0}
            color="#f59e0b"
            icon={HardDrive}
          />
        </div>

        <div className="grid-2 info-grid">
          <InfoCard
            title="Host hardware"
            icon={<Server size={20} color="var(--accent-primary)" />}
            rows={[
              { label: 'Processor', value: worker?.cpuModel ?? '—' },
              { label: 'Memory', value: worker ? `${worker.memTotalGb} GB` : '—' },
              {
                label: 'Docker storage',
                value: live ? `${diskFreeGb} GB free of ${diskTotalGb} GB` : '—'
              },
              { label: 'Docker data root', value: worker?.dockerDataRoot ?? 'Unavailable', mono: true },
              { label: 'Host uptime', value: worker ? formatUptime(worker.uptimeSec) : '—' }
            ]}
          />
          <InfoCard
            title="Worker identity"
            icon={<ShieldCheck size={20} color="var(--accent-primary)" />}
            rows={[
              { label: 'Worker ID', value: worker?.workerId ?? '—', mono: true },
              { label: 'Operating system', value: worker?.os ?? '—' },
              { label: 'Kernel / architecture', value: worker ? `${worker.kernel} · ${worker.arch}` : '—' },
              { label: 'Scheduler', value: worker?.schedulerUrl ?? '—', mono: true },
              {
                label: 'Assignment state',
                value: `${status?.mode ?? 'UNKNOWN'} · ${status?.activeAssignments ?? 0} active`,
                mono: true,
                color: connected ? 'var(--status-online)' : 'var(--status-pending)'
              }
            ]}
          />
        </div>

        <div className="gpus-grid">
          {gpus.length > 0 ? (
            gpus.map((gpu) => <GpuCard key={gpu.index} gpu={gpu} connected={live} cuda={cuda} />)
          ) : (
            <GpuCard
              gpu={{
                index: 0,
                name: worker?.gpuName ?? 'No GPU detected',
                load: gpuLoad,
                vramUsedGb,
                vramFreeGb,
                vramTotalGb,
                temperatureC: metrics?.gpuTempC ?? 0,
                inUse: false
              }}
              connected={live}
              cuda={cuda}
            />
          )}
        </div>

        <div className="chart-grid">
          <div className="card chart-card">
            <div className="card-header">
              <Activity size={19} color="var(--accent-primary)" />
              <h3>CPU</h3>
              <span className="chart-value">{live ? `${Math.round(cpuLoad)}%` : '—'}</span>
            </div>
            <Sparkline data={cpuHistory} />
          </div>
          <div className="card chart-card">
            <div className="card-header">
              <Gauge size={19} color="#2dd4bf" />
              <h3>GPU</h3>
              <span className="chart-value">{live ? `${Math.round(gpuLoad)}%` : '—'}</span>
            </div>
            <Sparkline data={gpuHistory} color="#2dd4bf" />
          </div>
          <div className="card chart-card">
            <div className="card-header">
              <Database size={19} color="#8b5cf6" />
              <h3>Disk read</h3>
              <span className="chart-value">{formatBytes(metrics?.diskReadBytesPerS ?? 0)}</span>
            </div>
            <Sparkline data={diskReadHistory} color="#8b5cf6" />
            <span className="chart-foot">{diskUsedPct.toFixed(0)}% storage used</span>
          </div>
          <div className="card chart-card">
            <div className="card-header">
              <Network size={19} color="#38bdf8" />
              <h3>Network receive</h3>
              <span className="chart-value">{formatBytes(metrics?.netRecvBytesPerS ?? 0)}</span>
            </div>
            <Sparkline data={netRecvHistory} color="#38bdf8" />
            <span className="chart-foot">sent {formatBytes(metrics?.netSentBytesPerS ?? 0)}</span>
          </div>
        </div>

        <JobsTable jobs={jobs} connected={apiReachable} />

        <section className="card service-log-card">
          <div className="card-header service-log-header">
            <TerminalSquare size={20} color="var(--accent-primary)" />
            <div>
              <h3>Worker service logs</h3>
              <p>Python worker activity only — training container output is intentionally excluded.</p>
            </div>
            <span className="badge badge-running">{logs.length} lines</span>
          </div>
          <div className="service-log-body mono">
            {logs.length === 0 ? (
              <div className="empty-state">
                <Clock3 size={24} />
                <span>{apiReachable ? 'Waiting for worker log activity.' : 'Worker service is offline.'}</span>
              </div>
            ) : (
              logs.map((entry, index) => (
                <div key={`${entry.timestamp}-${index}`} className={`service-log-line log-${entry.level}`}>
                  <span className="service-log-time">{entry.timestamp.slice(11, 19)}</span>
                  <span className="service-log-level">{entry.level.toUpperCase()}</span>
                  <span className="service-log-source">{entry.logger}</span>
                  <span className="service-log-message">{entry.message}</span>
                </div>
              ))
            )}
          </div>
        </section>
      </div>
    </div>
  )
}

export default Dashboard
