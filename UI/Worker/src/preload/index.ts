import { contextBridge } from 'electron'

const api = {
  platform: process.platform,
  workerApiUrl: process.env.WORKER_API_URL ?? 'http://127.0.0.1:8600',
  versions: {
    electron: process.versions.electron ?? '',
    chrome: process.versions.chrome ?? '',
    node: process.versions.node ?? ''
  }
} as const

if (process.contextIsolated) {
  try {
    contextBridge.exposeInMainWorld('worker', api)
  } catch (error) {
    console.error(error)
  }
} else {
  // @ts-ignore (define in index.d.ts)
  window.worker = api
}
