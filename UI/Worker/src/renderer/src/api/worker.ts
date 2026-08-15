import type {
  EventRecord,
  GpuInfo,
  JobRecord,
  Metrics,
  WorkerConfig,
  WorkerInfo,
  WorkerStatus
} from '../types'

export const WORKER_API_URL: string = window.worker?.workerApiUrl ?? 'http://127.0.0.1:8600'

export const WORKER_WS_URL: string = `${WORKER_API_URL.replace(/^http/, 'ws')}/ws/metrics`

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${WORKER_API_URL}${path}`, {
    signal: AbortSignal.timeout(4000)
  })
  if (!res.ok) throw new Error(`Worker API ${path} failed: ${res.status}`)
  return (await res.json()) as T
}

async function sendJson<T>(method: 'POST' | 'PUT', path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${WORKER_API_URL}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(4000)
  })
  if (!res.ok) throw new Error(`Worker API ${path} failed: ${res.status}`)
  return (await res.json()) as T
}

export function fetchWorker(): Promise<WorkerInfo> {
  return getJson<WorkerInfo>('/api/worker')
}

export function fetchMetrics(): Promise<Metrics> {
  return getJson<Metrics>('/api/metrics')
}

export function fetchGpus(): Promise<GpuInfo[]> {
  return getJson<GpuInfo[]>('/api/gpus')
}

export function fetchJobs(): Promise<JobRecord[]> {
  return getJson<JobRecord[]>('/api/jobs')
}

export function fetchEvents(): Promise<EventRecord[]> {
  return getJson<EventRecord[]>('/api/events')
}

export function fetchStatus(): Promise<WorkerStatus> {
  return getJson<WorkerStatus>('/api/status')
}

export function fetchConfig(): Promise<WorkerConfig> {
  return getJson<WorkerConfig>('/api/config')
}

export function updateConfig(patch: Partial<WorkerConfig>): Promise<WorkerConfig> {
  return sendJson<WorkerConfig>('PUT', '/api/config', patch)
}

export function pauseWorker(): Promise<WorkerStatus> {
  return sendJson<WorkerStatus>('POST', '/api/control/pause')
}

export function resumeWorker(): Promise<WorkerStatus> {
  return sendJson<WorkerStatus>('POST', '/api/control/resume')
}
