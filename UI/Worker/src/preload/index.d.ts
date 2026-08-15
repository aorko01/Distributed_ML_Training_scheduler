export interface WorkerApi {
  platform: string
  versions: {
    electron: string
    chrome: string
    node: string
  }
}

declare global {
  interface Window {
    worker: WorkerApi
  }
}
