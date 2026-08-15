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
  memTotalGb: number
  gpuCount: number
  gpuName: string
  gpuVramTotalGb: number
}

export interface JobRecord {
  id: string
  image: string
  type: 'training' | 'estimation'
  status: 'running' | 'completed' | 'failed'
  vramEstimateGb: number
  startedAt: string
  durationSec: number
}

export interface LogLine {
  time: string
  level: 'info' | 'warn' | 'error' | 'success'
  message: string
}

export const mockWorker: WorkerInfo = {
  workerId: 'wk-7f3a9c21-4bd8-b3d1-3e0a2f8c5d01',
  hostname: 'gpu-worker-01',
  ipAddress: '192.168.1.42',
  os: 'Ubuntu 24.04 LTS',
  platform: 'linux',
  arch: 'x64',
  schedulerUrl: 'http://192.168.1.10:8000',
  heartbeatIntervalSec: 5,
  jobPollIntervalSec: 10,
  dockerAvailable: true,
  cudaAvailable: true,
  cpus: 16,
  memTotalGb: 64,
  gpuCount: 1,
  gpuName: 'NVIDIA GeForce RTX 4090',
  gpuVramTotalGb: 24
}

export const mockJobs: JobRecord[] = [
  {
    id: 'job-a7f1e2',
    image: 'registry/job-a7f1e2:v3',
    type: 'estimation',
    status: 'completed',
    vramEstimateGb: 21.4,
    startedAt: '14:32:05',
    durationSec: 412
  },
  {
    id: 'job-9c2b44',
    image: 'registry/job-9c2b44:v1',
    type: 'training',
    status: 'completed',
    vramEstimateGb: 18.9,
    startedAt: '11:07:51',
    durationSec: 1733
  },
  {
    id: 'job-3d8f0a',
    image: 'registry/job-3d8f0a:v5',
    type: 'estimation',
    status: 'failed',
    vramEstimateGb: 0,
    startedAt: '09:41:12',
    durationSec: 96
  },
  {
    id: 'job-b5c7d9',
    image: 'registry/job-b5c7d9:v2',
    type: 'training',
    status: 'completed',
    vramEstimateGb: 15.2,
    startedAt: '08:15:40',
    durationSec: 2104
  }
]

export function formatDuration(sec: number): string {
  if (sec < 60) return `${sec}s`
  const m = Math.floor(sec / 60)
  const s = sec % 60
  if (m < 60) return `${m}m ${s}s`
  const h = Math.floor(m / 60)
  return `${h}h ${m % 60}m`
}
