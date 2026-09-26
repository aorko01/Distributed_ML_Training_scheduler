import { useRef, useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { interactive } from '../services/interactive';

interface Props {
  workspaceId: string;
  workspaceName: string;
  revisionId: string;
  onClose: () => void;
}

export function RevisionTrainingDialog({ workspaceId, workspaceName, revisionId, onClose }: Props) {
  const navigate = useNavigate();
  const [name, setName] = useState(`${workspaceName} training`);
  const [entryScript, setEntryScript] = useState('');
  const [retryScript, setRetryScript] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const requestKey = useRef<string | null>(null);
  const submitting = useRef(false);

  function changed() {
    requestKey.current = null;
    setError('');
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current || !name.trim() || !entryScript.trim()) return;
    submitting.current = true;
    setBusy(true);
    setError('');
    requestKey.current ??= crypto.randomUUID();
    try {
      const job = await interactive.submitRevisionTraining(workspaceId, requestKey.current, {
        name: name.trim(), command: entryScript.trim(), resume_command: retryScript.trim() || null,
        revision_id: revisionId,
      });
      onClose();
      navigate(`/jobs/${job.job_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Training submission failed');
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" onMouseDown={event => { if (event.target === event.currentTarget && !busy) onClose(); }}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="revision-training-title">
        <div className="modal-header"><h2 id="revision-training-title">Submit for training</h2></div>
        <form onSubmit={submit}>
          <div className="modal-body">
            <p className="iw-muted">Creates a new batch job from this session’s built image. Changes in the live container are not included.</p>
            <div className="form-group"><label className="form-label" htmlFor="training-name">New job name</label><input id="training-name" className="form-input" maxLength={120} required autoFocus value={name} onChange={event => { setName(event.target.value); changed(); }} /></div>
            <div className="form-group"><label className="form-label" htmlFor="training-entry">Entry script</label><input id="training-entry" className="form-input" maxLength={4096} required placeholder="python train.py" value={entryScript} onChange={event => { setEntryScript(event.target.value); changed(); }} /></div>
            <div className="form-group"><label className="form-label" htmlFor="training-retry">Retry script (optional)</label><input id="training-retry" className="form-input" maxLength={4096} placeholder="python resume.py" value={retryScript} onChange={event => { setRetryScript(event.target.value); changed(); }} /></div>
            {error && <p role="alert" className="error-text">{error}</p>}
          </div>
          <div className="modal-footer" style={{ justifyContent: 'flex-end', gap: '0.6rem' }}>
            <button type="button" className="btn btn-secondary" disabled={busy} onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={busy || !name.trim() || !entryScript.trim()}>{busy ? 'Submitting…' : 'Submit for training'}</button>
          </div>
        </form>
      </div>
    </div>
  );
}
