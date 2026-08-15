import React from 'react'
import { PlugZap, X } from 'lucide-react'

interface DisconnectModalProps {
  workerId: string
  onConfirm: () => void
  onCancel: () => void
}

const DisconnectModal: React.FC<DisconnectModalProps> = ({ workerId, onConfirm, onCancel }) => (
  <div className="modal-backdrop" onClick={onCancel}>
    <div className="modal" onClick={(e) => e.stopPropagation()}>
      <div className="modal-header">
        <h3 style={{ margin: 0 }}>Disconnect from Scheduler</h3>
        <button className="modal-close" onClick={onCancel} aria-label="Close">
          <X size={16} />
        </button>
      </div>
      <div className="modal-body">
        <div className="modal-icon">
          <PlugZap size={22} />
        </div>
        <p>
          This will stop heartbeats for worker <strong className="mono">{workerId}</strong>. The
          scheduler will no longer assign jobs to this machine until it reconnects.
        </p>
        <p className="modal-note">Dummy action — the worker process is not connected yet.</p>
      </div>
      <div className="modal-footer">
        <button className="btn btn-secondary btn-sm" onClick={onCancel}>
          Cancel
        </button>
        <button className="btn btn-danger btn-sm" onClick={onConfirm}>
          Disconnect
        </button>
      </div>
    </div>
  </div>
)

export default DisconnectModal
