import React, { useEffect, useMemo, useState } from 'react'
import Sidebar from './components/Sidebar'
import Dashboard from './views/Dashboard'
import PastJobs from './views/PastJobs'
import WorkerLogs from './views/WorkerLogs'
import { useWorkerData } from './hooks/useWorkerData'
import type { View } from './components/Sidebar'

const readView = (): View => {
  const candidate = window.location.hash.slice(1)
  return candidate === 'past-jobs' || candidate === 'worker-logs' ? candidate : 'dashboard'
}

const App: React.FC = () => {
  const data = useWorkerData()
  const platform = window.worker?.platform ?? 'unknown'
  const [view, setView] = useState<View>(readView)
  const counts = useMemo(
    () => ({
      running: data.jobs.filter((job) => job.status === 'running').length,
      past: data.jobs.filter((job) => job.status !== 'running').length,
      logs: data.logs.length
    }),
    [data.jobs, data.logs]
  )

  useEffect(() => {
    const syncView = () => setView(readView())
    window.addEventListener('hashchange', syncView)
    return () => window.removeEventListener('hashchange', syncView)
  }, [])

  const navigate = (nextView: View) => {
    if (nextView === view) return
    window.location.hash = nextView
    setView(nextView)
  }

  const page = (() => {
    switch (view) {
      case 'past-jobs':
        return <PastJobs {...data} />
      case 'worker-logs':
        return <WorkerLogs {...data} />
      default:
        return <Dashboard {...data} />
    }
  })()

  return (
    <div className="app-container">
      <Sidebar
        view={view}
        onViewChange={navigate}
        counts={counts}
        connected={data.connected}
        apiReachable={data.apiReachable}
        platform={platform}
      />

      <main className="main-content">
        {page}
      </main>
    </div>
  )
}

export default App
