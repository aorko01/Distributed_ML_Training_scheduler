import { useCallback, useEffect, useRef, useState } from 'react'
import {
  WORKER_WS_URL,
  fetchConfig,
  fetchEvents,
  fetchJobs,
  fetchMetrics,
  fetchGpus,
  fetchStatus,
  fetchWorker,
  pauseWorker,
  resumeWorker,
  updateConfig
} from '../api/worker'
import type {
  EventRecord,
  GpuInfo,
  JobRecord,
  Metrics,
  WorkerConfig,
  WorkerInfo,
  WorkerStatus
} from '../types'

const POLL_MS = 2500
const HISTORY_LEN = 48

function pushHistory(prev: number[], value: number): number[] {
  const next = [...prev, value]
  return next.length > HISTORY_LEN ? next.slice(-HISTORY_LEN) : next
}

export interface WorkerData {
  worker: WorkerInfo | null
  metrics: Metrics | null
  gpus: GpuInfo[]
  jobs: JobRecord[]
  events: EventRecord[]
  status: WorkerStatus | null
  config: WorkerConfig | null
  connected: boolean
  paused: boolean
  apiReachable: boolean
  cpuHistory: number[]
  gpuHistory: number[]
  diskReadHistory: number[]
  netRecvHistory: number[]
  lastHeartbeat: string
  error: string | null
  updateConfig: (patch: Partial<WorkerConfig>) => Promise<WorkerConfig>
  pauseWorker: () => Promise<void>
  resumeWorker: () => Promise<void>
}

export function useWorkerData(): WorkerData {
  const [worker, setWorker] = useState<WorkerInfo | null>(null)
  const [metrics, setMetrics] = useState<Metrics | null>(null)
  const [gpus, setGpus] = useState<GpuInfo[]>([])
  const [jobs, setJobs] = useState<JobRecord[]>([])
  const [events, setEvents] = useState<EventRecord[]>([])
  const [status, setStatus] = useState<WorkerStatus | null>(null)
  const [config, setConfig] = useState<WorkerConfig | null>(null)
  const [apiReachable, setApiReachable] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [wsActive, setWsActive] = useState(false)

  const [cpuHistory, setCpuHistory] = useState<number[]>([])
  const [gpuHistory, setGpuHistory] = useState<number[]>([])
  const [diskReadHistory, setDiskReadHistory] = useState<number[]>([])
  const [netRecvHistory, setNetRecvHistory] = useState<number[]>([])
  const wsActiveRef = useRef(false)

  useEffect(() => {
    wsActiveRef.current = wsActive
  }, [wsActive])

  useEffect(() => {
    let closed = false
    let ws: WebSocket | null = null
    let reconnectTimer: number | null = null

    const connect = () => {
      if (closed) return
      try {
        ws = new WebSocket(WORKER_WS_URL)
      } catch {
        return
      }

      ws.onopen = () => {
        setWsActive(true)
      }

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data as string) as {
            metrics?: Metrics
            gpus?: GpuInfo[]
          }
          if (data.metrics) {
            setMetrics(data.metrics)
            setCpuHistory((prev) => pushHistory(prev, data.metrics!.cpuLoad))
            setGpuHistory((prev) => pushHistory(prev, data.metrics!.gpuLoad))
            setDiskReadHistory((prev) => pushHistory(prev, data.metrics!.diskReadBytesPerS))
            setNetRecvHistory((prev) => pushHistory(prev, data.metrics!.netRecvBytesPerS))
          }
          if (data.gpus) setGpus(data.gpus)
        } catch {
          /* ignore malformed frames */
        }
      }

      ws.onclose = () => {
        setWsActive(false)
        if (!closed) reconnectTimer = window.setTimeout(connect, 3000)
      }

      ws.onerror = () => {
        setWsActive(false)
      }
    }

    connect()
    return () => {
      closed = true
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer)
      ws?.close()
    }
  }, [])

  useEffect(() => {
    let cancelled = false

    const tick = async () => {
      try {
        const [s, j, e] = await Promise.all([fetchStatus(), fetchJobs(), fetchEvents()])
        if (cancelled) return
        setStatus(s)
        setJobs(j)
        setEvents(e)
        setApiReachable(true)
        setError(null)

        if (!wsActiveRef.current) {
          const [m, g] = await Promise.all([fetchMetrics(), fetchGpus()])
          if (cancelled) return
          setMetrics(m)
          setGpus(g)
          setCpuHistory((prev) => pushHistory(prev, m.cpuLoad))
          setGpuHistory((prev) => pushHistory(prev, m.gpuLoad))
          setDiskReadHistory((prev) => pushHistory(prev, m.diskReadBytesPerS))
          setNetRecvHistory((prev) => pushHistory(prev, m.netRecvBytesPerS))
        }
      } catch (e) {
        if (cancelled) return
        setApiReachable(false)
        setError(e instanceof Error ? e.message : String(e))
      }
    }

    void tick()
    const interval = window.setInterval(tick, POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    fetchWorker()
      .then((w) => {
        if (!cancelled) setWorker(w)
      })
      .catch(() => undefined)
    fetchConfig()
      .then((c) => {
        if (!cancelled) setConfig(c)
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [])

  const handleUpdateConfig = useCallback(async (patch: Partial<WorkerConfig>) => {
    const updated = await updateConfig(patch)
    setConfig(updated)
    return updated
  }, [])

  const handlePause = useCallback(async () => {
    try {
      const s = await pauseWorker()
      setStatus(s)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  const handleResume = useCallback(async () => {
    try {
      const s = await resumeWorker()
      setStatus(s)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  const paused = status?.paused ?? false
  const connected = apiReachable && !paused && (status?.connected ?? false)
  const lastHeartbeat = status?.lastHeartbeatAt ?? '—'

  return {
    worker,
    metrics,
    gpus,
    jobs,
    events,
    status,
    config,
    connected,
    paused,
    apiReachable,
    cpuHistory,
    gpuHistory,
    diskReadHistory,
    netRecvHistory,
    lastHeartbeat,
    error,
    updateConfig: handleUpdateConfig,
    pauseWorker: handlePause,
    resumeWorker: handleResume
  }
}
