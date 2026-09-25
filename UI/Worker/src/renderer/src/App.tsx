import React from 'react'
import Sidebar from './components/Sidebar'
import Dashboard from './views/Dashboard'
import { useWorkerData } from './hooks/useWorkerData'

const App: React.FC = () => {
  const data = useWorkerData()
  const platform = window.worker?.platform ?? 'unknown'

  return (
    <div className="app-container">
      <Sidebar
        view="dashboard"
        connected={data.connected}
        apiReachable={data.apiReachable}
        platform={platform}
      />

      <main className="main-content">
        <Dashboard {...data} />
      </main>
    </div>
  )
}

export default App
