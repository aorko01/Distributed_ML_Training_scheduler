import React, { useState } from 'react'
import Sidebar, { type View } from './components/Sidebar'
import DisconnectModal from './components/DisconnectModal'
import Dashboard from './views/Dashboard'
import Logs from './views/Logs'
import { mockWorker } from './data/mock'

const App: React.FC = () => {
  const [view, setView] = useState<View>('dashboard')
  const [connected, setConnected] = useState(true)
  const [showModal, setShowModal] = useState(false)

  const handleConfirmDisconnect = () => {
    setConnected(false)
    setShowModal(false)
  }

  const platform = window.worker?.platform ?? 'unknown'

  return (
    <div className="app-container">
      <Sidebar
        view={view}
        onNavigate={setView}
        connected={connected}
        platform={platform}
      />

      <main className="main-content">
        {view === 'dashboard' ? (
          <Dashboard
            connected={connected}
            onDisconnect={() => setShowModal(true)}
            onReconnect={() => setConnected(true)}
          />
        ) : (
          <Logs connected={connected} />
        )}
      </main>

      {showModal ? (
        <DisconnectModal
          workerId={mockWorker.workerId}
          onConfirm={handleConfirmDisconnect}
          onCancel={() => setShowModal(false)}
        />
      ) : null}
    </div>
  )
}

export default App
