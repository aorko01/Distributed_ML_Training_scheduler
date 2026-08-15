import React, { useEffect, useRef, useState } from 'react'
import { Download, Terminal, Trash2 } from 'lucide-react'
import type { LogLine } from '../data/mock'

const INITIAL_LOGS: LogLine[] = [
  { time: '09:00:00', level: 'info', message: 'Worker agent starting up (worker id wk-7f3a…5d01)' },
  { time: '09:00:01', level: 'info', message: 'Loaded config: SCHEDULER_URL=http://192.168.1.10:8000' },
  { time: '09:00:01', level: 'success', message: 'Hardware probe: NVIDIA GeForce RTX 4090, 24 GB VRAM' },
  { time: '09:00:02', level: 'success', message: 'Registered with scheduler as gpu-worker-01 (192.168.1.42)' },
  { time: '09:00:02', level: 'info', message: 'Heartbeat thread started (interval 5s)' },
  { time: '09:00:02', level: 'info', message: 'Job polling thread started (interval 10s)' }
]

const LEVEL_CLASS: Record<LogLine['level'], string> = {
  info: 'log-info',
  warn: 'log-warn',
  error: 'log-error',
  success: 'log-success'
}

interface LogsProps {
  connected: boolean
}

const Logs: React.FC<LogsProps> = ({ connected }) => {
  const [logs, setLogs] = useState<LogLine[]>(INITIAL_LOGS)
  const [paused, setPaused] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (paused || !connected) return
    const interval = window.setInterval(() => {
      setLogs((prev) => {
        const time = new Date().toLocaleTimeString()
        const pool: LogLine[] = [
          { time, level: 'info', message: `Heartbeat sent — gpuLoad=${(Math.random() * 20).toFixed(1)}% vramFree=23.1 GB` },
          { time, level: 'info', message: 'No jobs available for allocation' },
          { time, level: 'info', message: 'Polled job queue — 0 matching jobs' }
        ]
        const next = [...prev, pool[Math.floor(Math.random() * pool.length)]]
        return next.length > 200 ? next.slice(-200) : next
      })
    }, 4000)
    return () => window.clearInterval(interval)
  }, [paused, connected])

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [logs])

  const handleDownload = () => {
    const blob = new Blob([logs.map((l) => `[${l.time}] [${l.level.toUpperCase()}] ${l.message}`).join('\n')], {
      type: 'text/plain'
    })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `worker-logs-${Date.now()}.log`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="dashboard">
      <div className="top-header">
        <div className="top-header-left">
          <h2 className="page-title">Logs</h2>
          <span className="top-header-sub">Worker agent console output</span>
        </div>
        <div className="top-header-actions">
          <button className="btn btn-secondary btn-sm" onClick={() => setPaused((p) => !p)}>
            <Terminal size={14} />
            {paused ? 'Resume' : 'Pause'}
          </button>
          <button className="btn btn-secondary btn-sm" onClick={() => setLogs([])}>
            <Trash2 size={14} />
            Clear
          </button>
          <button className="btn btn-secondary btn-sm" onClick={handleDownload}>
            <Download size={14} />
            Export
          </button>
        </div>
      </div>

      <div className="page-content fade-in">
        <div className="log-console">
          <div className="log-console-header">
            <span className="log-console-title">
              <Terminal size={14} />
              worker-agent.log
            </span>
            <span className={`badge ${connected ? 'badge-online' : 'badge-offline'}`}>
              {connected ? 'streaming' : 'stream paused'}
            </span>
          </div>
          <div className="log-console-body mono" ref={scrollRef}>
            {logs.length === 0 ? (
              <div className="empty-state">
                <Terminal size={24} />
                <span>No log entries.</span>
              </div>
            ) : (
              logs.map((line, i) => (
                <div key={i} className={`log-line ${LEVEL_CLASS[line.level]}`}>
                  <span className="log-time">[{line.time}]</span>
                  <span className="log-level">[{line.level.toUpperCase()}]</span>
                  <span className="log-message">{line.message}</span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export default Logs
