import React, { useState } from 'react'
import Sidebar from './components/Sidebar'
import ConfigModal from './components/ConfigModal'
import Dashboard from './views/Dashboard'
import { useWorkerData } from './hooks/useWorkerData'

const App: React.FC = () => {
  const data = useWorkerData()
  const [showConfig, setShowConfig] = useState(false)
  const platform = window.worker?.platform ?? 'unknown'

  return (
    <div className="app-container">
      <Sidebar view="dashboard" connected={data.connected} platform={platform} />

      <main className="main-content">
        <Dashboard {...data} onOpenConfig={() => setShowConfig(true)} />
      </main>

      {showConfig && data.config ? (
        <ConfigModal
          config={data.config}
          onSave={data.updateConfig}
          onClose={() => setShowConfig(false)}
        />
      ) : null}
    </div>
  )
}

export default App
