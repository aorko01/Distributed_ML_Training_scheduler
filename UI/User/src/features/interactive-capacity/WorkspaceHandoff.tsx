import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { interactive, InteractiveRequestError, type Runtime, type TrainingSubmission, type WorkspaceSave } from '../../services/interactive';
import { Dialog } from '../workspace/components/Dialog';

const activeSaveStates = new Set(['REQUESTED', 'CAPTURING', 'UPLOADING', 'PUBLISH_QUEUED', 'PUBLISHING']);
const activeTrainingStates = new Set(['SAVING', 'WAITING_FOR_REVISION']);

type TrainingSettings = { name: string; command: string; resume_command: string | null };
type TrainingDraft = { key: string; settings: TrainingSettings };

function readStored(key: string): string | null {
  try { return sessionStorage.getItem(key); } catch { return null; }
}

function store(key: string, value: string) {
  try { sessionStorage.setItem(key, value); } catch { /* Status still works until this page closes. */ }
}

function forget(key: string) {
  try { sessionStorage.removeItem(key); } catch { /* Storage may be unavailable. */ }
}

function readDraft(key: string): TrainingDraft | null {
  try {
    const value = readStored(key);
    return value ? JSON.parse(value) as TrainingDraft : null;
  } catch { return null; }
}

function requestKey(): string {
  const value = crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
  return value.replace(/[^A-Za-z0-9_-]/g, '').padEnd(16, '0').slice(0, 64);
}

function rejectedBeforeAcceptance(error: unknown): boolean {
  return error instanceof InteractiveRequestError && error.status >= 400 && error.status < 500 && error.status !== 409;
}

export function WorkspaceHandoff({ runtime, workspaceName, onActiveChange }: { runtime: Runtime; workspaceName: string; onActiveChange: (active: boolean) => void }) {
  const scope = `${runtime.id}:${runtime.generation}`;
  const saveIdKey = `dml-remote-save-id:${scope}`;
  const saveKey = `dml-remote-save-key:${scope}`;
  // Reuse the editor's submission id so an already started handoff stays visible.
  const trainingIdKey = `dml-submission-id:${scope}`;
  const trainingKey = `dml-submit-key:${scope}`;
  const draftKey = `dml-remote-training-draft:${scope}`;
  const [saveId, setSaveId] = useState(() => readStored(saveIdKey));
  const [save, setSave] = useState<WorkspaceSave | null>(null);
  const [trainingId, setTrainingId] = useState(() => readStored(trainingIdKey));
  const [training, setTraining] = useState<TrainingSubmission | null>(null);
  const [requesting, setRequesting] = useState<'save' | 'training' | null>(null);
  const [error, setError] = useState('');
  const [showTraining, setShowTraining] = useState(false);
  const initialDraft = readDraft(draftKey);
  const [name, setName] = useState(initialDraft?.settings.name ?? `${workspaceName} training`);
  const [command, setCommand] = useState(initialDraft?.settings.command ?? 'python train.py');
  const [resumeCommand, setResumeCommand] = useState(initialDraft?.settings.resume_command ?? '');

  useEffect(() => {
    if (!saveId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const status = await interactive.saveStatus(saveId!);
        if (cancelled) return;
        setSave(status);
        if (status.state === 'SUCCEEDED' || status.state === 'FAILED') forget(saveKey);
        else timer = setTimeout(() => void poll(), 3000);
      } catch {
        if (!cancelled) timer = setTimeout(() => void poll(), 5000);
      }
    }
    void poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [saveId, saveKey]);

  useEffect(() => {
    if (!trainingId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const status = await interactive.trainingStatus(trainingId!);
        if (cancelled) return;
        setTraining(status);
        if (status.state === 'JOB_CREATED' || status.state === 'FAILED') {
          forget(trainingKey);
          forget(draftKey);
        } else timer = setTimeout(() => void poll(), 3000);
      } catch {
        if (!cancelled) timer = setTimeout(() => void poll(), 5000);
      }
    }
    void poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [trainingId, trainingKey, draftKey]);

  const saving = !!saveId && (!save || activeSaveStates.has(save.state));
  const submitting = !!trainingId && (!training || activeTrainingStates.has(training.state));
  const operationBusy = requesting !== null || saving || submitting;
  const pendingSaveRequest = !saveId && !!readStored(saveKey);
  const pendingTrainingRequest = !trainingId && !!readDraft(draftKey);
  const stopBlocked = operationBusy || pendingSaveRequest || pendingTrainingRequest;
  useEffect(() => { onActiveChange(stopBlocked); return () => onActiveChange(false); }, [stopBlocked, onActiveChange]);

  const ready = runtime.state === 'READY' && runtime.desired_state === 'RUNNING';
  const canSave = ready && runtime.editor_capable === true && runtime.save_enabled === true;
  const canTrain = canSave && runtime.training_submission_enabled === true;
  const closeTraining = useCallback(() => { if (!requesting) setShowTraining(false); }, [requesting]);

  async function saveForLater() {
    if (!(canSave || pendingSaveRequest) || operationBusy) return;
    setError(''); setRequesting('save'); setSave(null);
    const key = readStored(saveKey) ?? requestKey();
    store(saveKey, key);
    try {
      const operation = await interactive.saveWorkspace(runtime.id, key, runtime.generation, runtime.revision_id);
      store(saveIdKey, operation.id);
      setSave(operation);
      setSaveId(operation.id);
    } catch (err) {
      if (rejectedBeforeAcceptance(err)) forget(saveKey);
      setError(err instanceof Error ? err.message : 'Could not request a save. Try again.');
    } finally { setRequesting(null); }
  }

  async function submitTraining() {
    if (!(canTrain || pendingTrainingRequest) || operationBusy) return;
    const settings: TrainingSettings = { name: name.trim(), command: command.trim(), resume_command: resumeCommand.trim() || null };
    if (!settings.name || !settings.command) {
      setError('Enter a job name and training script command.');
      return;
    }
    const previous = readDraft(draftKey);
    if (previous && JSON.stringify(previous.settings) !== JSON.stringify(settings)) {
      setError('A submission may already be in progress. Retry it with the original settings before changing them.');
      return;
    }
    const key = previous?.key ?? readStored(trainingKey) ?? requestKey();
    store(trainingKey, key);
    store(draftKey, JSON.stringify({ key, settings }));
    setError(''); setRequesting('training'); setTraining(null);
    try {
      const operation = await interactive.submitTraining(runtime.id, key, runtime.generation, runtime.revision_id, settings);
      store(trainingIdKey, operation.id);
      setTraining(operation);
      setTrainingId(operation.id);
      setShowTraining(false);
    } catch (err) {
      // A network failure is ambiguous. Retain the key and settings so a retry
      // cannot create a second batch job if the first request reached the API.
      if (rejectedBeforeAcceptance(err)) {
        forget(trainingKey);
        forget(draftKey);
      }
      setError(err instanceof Error ? err.message : 'Could not submit training. Try again.');
    } finally { setRequesting(null); }
  }

  if (!ready && !saveId && !trainingId && !pendingSaveRequest && !pendingTrainingRequest) return null;

  return (
    <section className="card iw-step" aria-label="Save and train">
      <header className="iw-step-head">
        <span className="iw-step-no">4</span>
        <div>
          <h2>Save and train</h2>
          <p className="iw-muted">Save files in VS Code first. The image captures files and installed packages from the running container.</p>
        </div>
      </header>
      {(ready || pendingSaveRequest || pendingTrainingRequest) && (
        <div className="iw-session-foot">
          <button className="btn btn-secondary" disabled={!(canSave || pendingSaveRequest) || operationBusy} onClick={() => void saveForLater()}>
            {requesting === 'save' ? 'Requesting save…' : pendingSaveRequest ? 'Retry save request' : 'Save for Later'}
          </button>
          <button className="btn btn-primary" disabled={!(canTrain || pendingTrainingRequest) || operationBusy} onClick={() => { setError(''); setShowTraining(true); }}>
            {pendingTrainingRequest ? 'Retry training submission' : 'Submit for Training'}
          </button>
        </div>
      )}
      {ready && !canSave && <p className="iw-muted">Saving is unavailable for this runtime.</p>}
      {ready && canSave && !canTrain && <p className="iw-muted">Training submission is unavailable for this runtime.</p>}
      {saveId && <p role="status" className="iw-muted">Save: {save?.state ?? 'Checking status…'}{save?.state === 'SUCCEEDED' ? ' — revision saved.' : null}{save?.failure_detail ? ` — ${save.failure_detail}` : null}</p>}
      {trainingId && <p role="status" className="iw-muted">Training submission: {training?.state ?? 'Checking status…'}{training?.failure_detail ? ` — ${training.failure_detail}` : null}{training?.job_id ? <> — <Link to={`/jobs/${training.job_id}`}>View job</Link></> : null}</p>}
      {error && !showTraining && <p role="alert" className="error-text">{error}</p>}
      {showTraining && <Dialog title="Submit for training" onClose={closeTraining} onConfirm={() => void submitTraining()} confirmLabel={requesting === 'training' ? 'Submitting…' : 'Save image and start training'}>
        <p>Save all files in VS Code before continuing. The current container and installed packages will be captured for batch training.</p>
        <label>Job name<input autoFocus value={name} maxLength={120} onChange={(event) => setName(event.target.value)} /></label>
        <label>Training script command<input value={command} maxLength={1024} onChange={(event) => setCommand(event.target.value)} placeholder="python train.py --epochs 10" /></label>
        <label>Resume script command (optional)<input value={resumeCommand} maxLength={1024} onChange={(event) => setResumeCommand(event.target.value)} placeholder="python resume.py --checkpoint checkpoint.pt" /></label>
        {error && <p role="alert" className="error-text">{error}</p>}
      </Dialog>}
    </section>
  );
}
