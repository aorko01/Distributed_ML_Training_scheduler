import React, { useEffect, useState } from 'react';
import { X, Package, Loader2, AlertCircle } from 'lucide-react';
import type { Job } from '../services/jobs';
import { commitInteractiveJob, type CommitInteractivePayload } from '../services/jobs';

interface CommitModalProps {
  job: Job;
  onClose: () => void;
  onCommitted: () => void;
}

const CommitModal: React.FC<CommitModalProps> = ({ job, onClose, onCommitted }) => {
  const [command, setCommand] = useState('');
  const [resumeCommand, setResumeCommand] = useState('');
  const [priority, setPriority] = useState<'NORMAL' | 'REQUESTED' | 'HIGH'>('NORMAL');
  const [reasonForPriority, setReasonForPriority] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onClose]);

  const handleSubmit = async () => {
    if (!command.trim()) {
      setError('Run command is required');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const payload: CommitInteractivePayload = {
        command: command.trim(),
        priority,
      };
      if (resumeCommand.trim()) {
        payload.resumeCommand = resumeCommand.trim();
      }
      if ((priority === 'REQUESTED' || priority === 'HIGH') && reasonForPriority.trim()) {
        payload.reasonForPriority = reasonForPriority.trim();
      }
      await commitInteractiveJob(job.id, payload);
      onCommitted();
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to commit session';
      setError(message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal"
        style={{ maxWidth: 560 }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <Package size={20} style={{ color: 'var(--accent-primary)' }} />
            <div>
              <h3 style={{ margin: 0 }}>Commit "{job.name}"</h3>
              <p style={{ margin: 0, fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                Save the current container state as a training image
              </p>
            </div>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>

        <div className="modal-body" style={{ paddingTop: '1.25rem' }}>
          {error && (
            <div className="alert alert-error" style={{ marginBottom: '1rem' }}>
              <AlertCircle size={14} />
              <span>{error}</span>
            </div>
          )}

          <div style={{ marginBottom: '1rem' }}>
            <label className="form-label">Run Command *</label>
            <textarea
              className="form-textarea"
              placeholder="e.g. python train.py --epochs 100 --lr 0.001"
              value={command}
              onChange={(e) => setCommand(e.target.value)}
              rows={3}
              style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: '0.85rem' }}
            />
            <p style={{ margin: '0.3rem 0 0', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
              The training command to run in batch mode
            </p>
          </div>

          <div style={{ marginBottom: '1rem' }}>
            <label className="form-label">Resume Checkpoint Command</label>
            <textarea
              className="form-textarea"
              placeholder="e.g. python train.py --resume checkpoints/latest.pt"
              value={resumeCommand}
              onChange={(e) => setResumeCommand(e.target.value)}
              rows={2}
              style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: '0.85rem' }}
            />
            <p style={{ margin: '0.3rem 0 0', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
              Optional: command to resume from a checkpoint after failure
            </p>
          </div>

          <div style={{ marginBottom: '1rem' }}>
            <label className="form-label">Priority</label>
            <select
              className="form-select"
              value={priority}
              onChange={(e) => setPriority(e.target.value as 'NORMAL' | 'REQUESTED' | 'HIGH')}
            >
              <option value="NORMAL">Normal</option>
              <option value="REQUESTED">Requested</option>
              <option value="HIGH">High</option>
            </select>
          </div>

          {(priority === 'REQUESTED' || priority === 'HIGH') && (
            <div style={{ marginBottom: '1rem' }}>
              <label className="form-label">Reason for Priority</label>
              <textarea
                className="form-textarea"
                placeholder="Explain why priority is needed..."
                value={reasonForPriority}
                onChange={(e) => setReasonForPriority(e.target.value)}
                rows={2}
              />
            </div>
          )}

          <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'flex-end', marginTop: '1.5rem' }}>
            <button
              className="btn btn-secondary"
              onClick={onClose}
              disabled={submitting}
            >
              Cancel
            </button>
            <button
              className="btn btn-primary"
              onClick={handleSubmit}
              disabled={submitting || !command.trim()}
              style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}
            >
              {submitting ? (
                <>
                  <Loader2 size={16} className="animate-spin" />
                  Committing...
                </>
              ) : (
                <>
                  <Package size={16} />
                  Commit & Push
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default CommitModal;
