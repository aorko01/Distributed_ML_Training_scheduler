export interface WorkerInfo {
  workerId: string
  hostname: string
  ipAddress: string
  os: string
  platform: string
  arch: string
  schedulerUrl: string
  heartbeatIntervalSec: number
  jobPollIntervalSec: number
  dockerAvailable: boolean
  cudaAvailable: boolean
  cpus: number
  cpuModel: string
  memTotalGb: number
  diskTotalGb: number
  diskFreeGb: number
  dockerDataRoot: string | null
  kernel: string
  uptimeSec: number
  gpuCount: number
  gpuName: string
  gpuVramTotalGb: number
}

export interface Metrics {
  cpuLoad: number
  memUsage: number
  memTotalGb: number
  gpuLoad: number
  vramUsedGb: number
  vramFreeGb: number
  vramTotalGb: number
  gpuTempC: number
  gpusInUse: number
  diskReadBytesPerS: number
  diskWriteBytesPerS: number
  netRecvBytesPerS: number
  netSentBytesPerS: number
  diskTotalGb: number
  diskFreeGb: number
  timestamp: number
}

export interface GpuInfo {
  index: number
  name: string
  load: number
  vramUsedGb: number
  vramFreeGb: number
  vramTotalGb: number
  temperatureC: number
  inUse: boolean
}

export interface JobRecord {
  id: string
  image: string
  type: 'training' | 'estimation' | 'interactive'
  status: 'running' | 'completed' | 'failed'
  vramEstimateGb: number
  startedAt: string
  durationSec: number
  assignmentId: string | null
  phase: string | null
}

export interface WorkerLogRecord {
  timestamp: string
  level: string
  logger: string
  message: string
}

export interface EventRecord {
  time: string
  level: 'info' | 'warn' | 'error' | 'success'
  message: string
}

export interface WorkerStatus {
  connected: boolean
  lastHeartbeatAt: string | null
  schedulerUrl: string
  paused: boolean
  acceptingJobs: boolean
  mode: string
  activeAssignments: number
}

export interface WorkerConfig {
  schedulerUrl: string
  heartbeatIntervalSec: number
  jobPollIntervalSec: number
  logPushIntervalSec: number
  logUploadIntervalSec: number
}

export function formatDuration(sec: number): string {
  if (sec < 60) return `${sec}s`
  const m = Math.floor(sec / 60)
  const s = sec % 60
  if (m < 60) return `${m}m ${s}s`
  const h = Math.floor(m / 60)
  return `${h}h ${m % 60}m`
}

export function formatBytes(bytesPerS: number): string {
  if (bytesPerS <= 0) return '0 B/s'
  if (bytesPerS < 1024) return `${bytesPerS.toFixed(0)} B/s`
  if (bytesPerS < 1024 * 1024) return `${(bytesPerS / 1024).toFixed(1)} KB/s`
  if (bytesPerS < 1024 * 1024 * 1024) return `${(bytesPerS / (1024 * 1024)).toFixed(1)} MB/s`
  return `${(bytesPerS / (1024 * 1024 * 1024)).toFixed(2)} GB/s`
}

export function formatUptime(seconds: number): string {
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (days > 0) return `${days}d ${hours}h`
  if (hours > 0) return `${hours}h ${minutes}m`
  return `${minutes}m`
}
