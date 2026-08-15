import React, { useState } from 'react'
import { Loader2, Save, Settings, X } from 'lucide-react'
import type { WorkerConfig } from '../types'

interface ConfigModalProps {
  config: WorkerConfig
  onSave: (patch: Partial<WorkerConfig>) => Promise<unknown>
  onClose: () => void
}

const ConfigModal: React.FC<ConfigModalProps> = ({ config, onSave, onClose }) => {
  const [schedulerUrl, setSchedulerUrl] = useState(config.schedulerUrl)
  const [heartbeat, setHeartbeat] = useState(String(config.heartbeatIntervalSec))
  const [poll, setPoll] = useState(String(config.jobPollIntervalSec))
  const [logPush, setLogPush] = useState(String(config.logPushIntervalSec))
  const [logUpload, setLogUpload] = useState(String(config.logUploadIntervalSec))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    try {
      await onSave({
        schedulerUrl,
        heartbeatIntervalSec: Number(heartbeat),
        jobPollIntervalSec: Number(poll),
        logPushIntervalSec: Number(logPush),
        logUploadIntervalSec: Number(logUpload)
      })
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal modal-config" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3 style={{ margin: 0 }}>Worker Settings</h3>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            <X size={16} />
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="modal-body config-body">
            <div className="config-hint">
              <Settings size={14} />
              Interval changes apply immediately. Scheduler URL applies immediately and persists in
              the worker .env file.
            </div>

            <div className="config-field">
              <label className="config-label" htmlFor="cfg-scheduler">Scheduler URL</label>
              <input
                id="cfg-scheduler"
                className="config-input mono"
                value={schedulerUrl}
                onChange={(e) => setSchedulerUrl(e.target.value)}
                placeholder="http://scheduler:8000"
              />
            </div>

            <div className="config-row">
              <div className="config-field">
                <label className="config-label" htmlFor="cfg-heartbeat">Heartbeat (s)</label>
                <input
                  id="cfg-heartbeat"
                  className="config-input"
                  type="number"
                  min="1"
                  step="1"
                  value={heartbeat}
                  onChange={(e) => setHeartbeat(e.target.value)}
                />
              </div>
              <div className="config-field">
                <label className="config-label" htmlFor="cfg-poll">Job Poll (s)</label>
                <input
                  id="cfg-poll"
                  className="config-input"
                  type="number"
                  min="1"
                  step="1"
                  value={poll}
                  onChange={(e) => setPoll(e.target.value)}
                />
              </div>
            </div>

            <div className="config-row">
              <div className="config-field">
                <label className="config-label" htmlFor="cfg-logpush">Log Push (s)</label>
                <input
                  id="cfg-logpush"
                  className="config-input"
                  type="number"
                  min="0.1"
                  step="0.1"
                  value={logPush}
                  onChange={(e) => setLogPush(e.target.value)}
                />
              </div>
              <div className="config-field">
                <label className="config-label" htmlFor="cfg-logupload">Log Upload (s)</label>
                <input
                  id="cfg-logupload"
                  className="config-input"
                  type="number"
                  min="1"
                  step="1"
                  value={logUpload}
                  onChange={(e) => setLogUpload(e.target.value)}
                />
              </div>
            </div>

            {error ? <div className="config-error">{error}</div> : null}
          </div>

          <div className="modal-footer">
            <button type="button" className="btn btn-secondary btn-sm" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary btn-sm" disabled={saving}>
              {saving ? <Loader2 size={14} className="spin" /> : <Save size={14} />}
              Save
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default ConfigModal
