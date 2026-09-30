import React, { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowDownToLine, Clock3, Pause, Play, Search, TerminalSquare } from 'lucide-react'
import TopBar from '../components/TopBar'
import type { WorkerData } from '../hooks/useWorkerData'

type LogLevel = 'all' | 'info' | 'warning' | 'error'

const normalizeLevel = (level: string): Exclude<LogLevel, 'all'> => {
  const normalized = level.toLowerCase()
  if (normalized === 'error' || normalized === 'critical') return 'error'
  if (normalized === 'warning' || normalized === 'warn') return 'warning'
  return 'info'
}

const WorkerLogs: React.FC<WorkerData> = ({
  worker,
  logs,
  connected,
  apiReachable,
  acceptingJobs,
  updatingAcceptingJobs,
  lastHeartbeat,
  toggleAcceptingJobs
}) => {
  const [level, setLevel] = useState<LogLevel>('all')
  const [query, setQuery] = useState('')
  const [autoScroll, setAutoScroll] = useState(true)
  const logBodyRef = useRef<HTMLDivElement>(null)

  const visibleLogs = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase()
    return logs.filter((entry) => {
      if (level !== 'all' && normalizeLevel(entry.level) !== level) return false
      if (!normalizedQuery) return true
      return `${entry.timestamp} ${entry.level} ${entry.logger} ${entry.message}`
        .toLowerCase()
        .includes(normalizedQuery)
    })
  }, [level, logs, query])

  useEffect(() => {
    if (logBodyRef.current && autoScroll) {
      logBodyRef.current.scrollTop = logBodyRef.current.scrollHeight
    }
  }, [autoScroll, visibleLogs])

  const scrollToLatest = () => {
    setAutoScroll(true)
    requestAnimationFrame(() => {
      if (logBodyRef.current) logBodyRef.current.scrollTop = logBodyRef.current.scrollHeight
    })
  }

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

      <div className="page-content logs-page fade-in">
        <section className="page-intro">
          <div className="page-intro-icon"><TerminalSquare size={22} /></div>
          <div>
            <span className="eyebrow">SERVICE DIAGNOSTICS</span>
            <h1>Worker logs</h1>
            <p>Live Python worker activity. Training container output is intentionally excluded.</p>
          </div>
          <span className={`live-indicator ${apiReachable ? 'is-live' : ''}`}>
            <span />
            {apiReachable ? 'Live feed' : 'Offline'}
          </span>
        </section>

        <div className="view-toolbar logs-toolbar" aria-label="Worker log filters">
          <div className="segmented-control">
            {(['all', 'info', 'warning', 'error'] as const).map((value) => (
              <button
                key={value}
                type="button"
                className={level === value ? 'active' : ''}
                aria-pressed={level === value}
                onClick={() => setLevel(value)}
              >
                {value}
              </button>
            ))}
          </div>
          <label className="search-field log-search-field">
            <Search size={16} />
            <span className="sr-only">Search worker logs</span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search logs"
            />
          </label>
          <button
            type="button"
            className={`btn btn-secondary btn-sm auto-scroll-button${autoScroll ? ' active' : ''}`}
            onClick={() => setAutoScroll((enabled) => !enabled)}
          >
            {autoScroll ? <Pause size={14} /> : <Play size={14} />}
            Auto-scroll {autoScroll ? 'on' : 'off'}
          </button>
        </div>

        <section className="service-log-card terminal-window logs-terminal">
          <div className="terminal-header service-log-header">
            <div className="mac-btns" aria-hidden="true">
              <span className="mac-btn close" />
              <span className="mac-btn minimize" />
              <span className="mac-btn maximize" />
            </div>
            <TerminalSquare size={19} color="var(--accent-primary)" />
            <div className="terminal-title-wrap">
              <h3>worker.service</h3>
              <p className="mono">{visibleLogs.length} of {logs.length} lines</p>
            </div>
            <button type="button" className="terminal-latest" onClick={scrollToLatest}>
              <ArrowDownToLine size={14} />
              Latest
            </button>
          </div>
          <div
            ref={logBodyRef}
            className="service-log-body terminal-body mono logs-terminal-body"
            onScroll={() => {
              if (!logBodyRef.current || !autoScroll) return
              const { scrollTop, scrollHeight, clientHeight } = logBodyRef.current
              if (scrollHeight - scrollTop - clientHeight > 16) setAutoScroll(false)
            }}
          >
            {visibleLogs.length === 0 ? (
              <div className="empty-state log-empty-state">
                <Clock3 size={26} />
                <strong>
                  {!apiReachable
                    ? 'Worker service is offline.'
                    : logs.length === 0
                      ? 'Waiting for worker log activity.'
                      : 'No log lines match these filters.'}
                </strong>
              </div>
            ) : (
              visibleLogs.map((entry, index) => (
                <div key={`${entry.timestamp}-${index}`} className={`service-log-line log-${entry.level.toLowerCase()}`}>
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

export default WorkerLogs
