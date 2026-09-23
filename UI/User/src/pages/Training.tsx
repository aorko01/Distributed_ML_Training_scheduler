import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { fetchJobById, submitTraining, type Job } from '../services/jobs';
import CopyButton from '../components/CopyButton';
import StatusBadge from '../components/StatusBadge';
import {
  ClipboardList, Rocket, Loader2, TerminalSquare, FileArchive, AlertTriangle, ArrowRight,
} from 'lucide-react';

/**
 * Training page - the second half of the decoupled workflow.
 *
 * "Add Workspace" only builds an image (no entry command). Its job id is copied
 * from the Builds page, pasted here together with the entry and resume commands,
 * and submitting arms the built image: the job then goes through VRAM
 * estimation and finally trains on a worker.
 */
const Training: React.FC = () => {
  const [params] = useSearchParams();
  const [jobId, setJobId] = useState(params.get('job') ?? '');
  const [job, setJob] = useState<Job | null>(null);
  const [looking, setLooking] = useState(false);
  const [lookupError, setLookupError] = useState('');
  const [command, setCommand] = useState('');
  const [resumeCommand, setResumeCommand] = useState('');
  const [requestPriority, setRequestPriority] = useState(false);
  const [priorityReason, setPriorityReason] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const submittingRef = useRef(false);
  const navigate = useNavigate();

  const trimmedId = jobId.trim();

  // Look the workspace up as soon as an id is present so the page can show what
  // the built image contains and whether it is ready to be armed.
  useEffect(() => {
    setJob(null);
    setLookupError('');
    setSubmitError('');
    if (!trimmedId) {
      setLooking(false);
      return;
    }
    let active = true;
    setLooking(true);
    fetchJobById(trimmedId)
      .then((data) => {
        if (!active) return;
        if (!data) {
          setLookupError(`No workspace with id ${trimmedId}`);
          return;
        }
        setJob(data);
        setCommand((current) => current || data.command || '');
        setResumeCommand((current) => current || data.resumeCommand || '');
      })
      .catch((err) => {
        if (active) setLookupError(err instanceof Error ? err.message : 'Lookup failed');
      })
      .finally(() => { if (active) setLooking(false); });
    return () => { active = false; };
  }, [trimmedId]);

  const readiness = useMemo(() => {
    if (!trimmedId) return { ready: false, reason: 'Paste the job id of a built workspace.' };
    if (looking) return { ready: false, reason: 'Looking up the workspace...' };
    if (lookupError) return { ready: false, reason: lookupError };
    if (!job) return { ready: false, reason: 'Workspace not loaded yet.' };
    switch (job.status) {
      case 'ImageReady':
        return { ready: true, reason: '' };
      case 'Pending':
        return { ready: false, reason: 'The image is still queued for building.' };
      case 'Building':
        return { ready: false, reason: 'The image is still building.' };
      case 'Estimating':
        return { ready: false, reason: 'Training was already submitted; VRAM estimation is running.' };
      case 'Running':
        return { ready: false, reason: 'This workspace is already training.' };
      default:
        return { ready: false, reason: `This workspace is ${job.status}; its image cannot be armed.` };
    }
  }, [job, looking, lookupError, trimmedId]);

  const canSubmit = readiness.ready && command.trim().length > 0 && !submitting;

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (submittingRef.current || !canSubmit) return;
    submittingRef.current = true;
    setSubmitting(true);
    setSubmitError('');
    try {
      await submitTraining(trimmedId, {
        command: command.trim(),
        resumeCommand,
        requestForPriority: requestPriority,
        reasonForPriority: priorityReason,
      });
      // VRAM estimation starts immediately; the job view shows both phases.
      navigate(`/jobs/${trimmedId}`);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : 'Training submission failed');
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  return (
    <div className="fade-in ws-add">
      <TrainingHero job={job} />
      <div className="card ws-card">
        <form onSubmit={handleSubmit}>
          <fieldset disabled={submitting} style={{ border: 0, padding: 0 }}>
            <JobIdField
              jobId={jobId}
              setJobId={setJobId}
              trimmedId={trimmedId}
              looking={looking}
              lookupError={lookupError}
              job={job}
              readinessReason={readiness.ready ? '' : readiness.reason}
            />
            <CommandFields
              command={command}
              setCommand={setCommand}
              resumeCommand={resumeCommand}
              setResumeCommand={setResumeCommand}
            />
            <PriorityField
              requestPriority={requestPriority}
              setRequestPriority={setRequestPriority}
              priorityReason={priorityReason}
              setPriorityReason={setPriorityReason}
            />
            {submitError && <div className="training-error" role="alert">{submitError}</div>}
            <SubmitRow canSubmit={canSubmit} submitting={submitting} jobId={job?.id} />
          </fieldset>
        </form>
      </div>
    </div>
  );
};

interface TrainingHeroProps {
  job: Job | null;
}

/** Summary strip: which workspace is about to be armed and in what state. */
const TrainingHero: React.FC<TrainingHeroProps> = ({ job }) => (
  <div className="ws-hero">
    <div className="ws-hero-copy">
      <span className="ws-eyebrow"><Rocket size={14} /> Training</span>
      <h1>Start Training</h1>
      <p>
        Workspace images are built without a command. Paste the job id copied from the
        Builds page, provide the entry and resume commands, and submit - the scheduler
        estimates VRAM and then runs the training on a worker.
      </p>
      <div className="ws-steps">
        <span className="ws-step"><ClipboardList size={14} /> 1 - Job id</span>
        <span className="ws-step"><TerminalSquare size={14} /> 2 - Entry command</span>
        <span className="ws-step"><FileArchive size={14} /> 3 - Resume command</span>
        <span className="ws-step"><Rocket size={14} /> 4 - Submit</span>
      </div>
    </div>
    <div className="ws-hero-card">
      <div className="ws-hero-row">
        <ClipboardList size={16} /><span>Workspace</span><strong>{job ? job.name : 'Not loaded'}</strong>
      </div>
      <div className="ws-hero-row">
        <TerminalSquare size={16} /><span>Status</span>
        <strong>{job ? <StatusBadge status={job.status} /> : 'Unknown'}</strong>
      </div>
      <div className="ws-hero-row">
        <FileArchive size={16} /><span>Environment</span>
        <strong>{job ? `PT ${job.pytorchVersion} / CUDA ${job.cudaVersion}` : 'Unknown'}</strong>
      </div>
    </div>
  </div>
);

interface JobIdFieldProps {
  jobId: string;
  setJobId: (value: string) => void;
  trimmedId: string;
  looking: boolean;
  lookupError: string;
  job: Job | null;
  readinessReason: string;
}

/** Job id entry field with copy support and a live readiness hint. */
const JobIdField: React.FC<JobIdFieldProps> = ({
  jobId, setJobId, trimmedId, looking, lookupError, job, readinessReason,
}) => (
  <div className="form-group">
    <label className="form-label" htmlFor="training-job-id">Workspace job id</label>
    <div className="training-id-row">
      <input
        id="training-job-id"
        className="form-input training-id-input"
        value={jobId}
        onChange={(e) => setJobId(e.target.value)}
        placeholder="Paste the job id from the Builds page"
        spellCheck={false}
        autoComplete="off"
        required
      />
      <CopyButton value={trimmedId} label="Copy" title="Copy this job id" />
    </div>
    <p className="ws-hint">
      Copy the id from <strong>Builds</strong> (or from a build detail page) and paste it here.
    </p>
    {looking && (
      <p role="status" className="ws-hint training-inline">
        <Loader2 size={14} className="animate-spin" /> Looking up workspace...
      </p>
    )}
    {!looking && lookupError && (
      <p role="alert" className="training-warning"><AlertTriangle size={14} /> {lookupError}</p>
    )}
    {!looking && job && readinessReason && (
      <p role="status" className="training-warning"><AlertTriangle size={14} /> {readinessReason}</p>
    )}
  </div>
);

interface CommandFieldsProps {
  command: string;
  setCommand: (value: string) => void;
  resumeCommand: string;
  setResumeCommand: (value: string) => void;
}

const CommandFields: React.FC<CommandFieldsProps> = ({
  command, setCommand, resumeCommand, setResumeCommand,
}) => (
  <>
    <div className="form-group">
      <label className="form-label" htmlFor="training-command">Entry command (Bash)</label>
      <textarea
        id="training-command"
        className="form-textarea"
        value={command}
        onChange={(e) => setCommand(e.target.value)}
        placeholder="python train.py --epochs 100 --batch-size 32"
        spellCheck={false}
        required
      />
      <p className="ws-hint">Single line, executed inside the container root of your built workspace image.</p>
    </div>
    <div className="form-group">
      <label className="form-label" htmlFor="training-resume-command">Resume checkpoint command (Bash)</label>
      <textarea
        id="training-resume-command"
        className="form-textarea"
        value={resumeCommand}
        onChange={(e) => setResumeCommand(e.target.value)}
        placeholder="python train.py --resume checkpoint.pt"
        spellCheck={false}
      />
      <p className="ws-hint">Optional. Used to resume from the last saved checkpoint when a run is retried.</p>
    </div>
  </>
);

interface PriorityFieldProps {
  requestPriority: boolean;
  setRequestPriority: (value: boolean) => void;
  priorityReason: string;
  setPriorityReason: (value: string) => void;
}

/** Priority request moved here from Add Workspace: it affects training scheduling only. */
const PriorityField: React.FC<PriorityFieldProps> = ({
  requestPriority, setRequestPriority, priorityReason, setPriorityReason,
}) => (
  <div className="form-group training-priority">
    <label className="training-checkbox">
      <input
        type="checkbox"
        checked={requestPriority}
        onChange={(e) => setRequestPriority(e.target.checked)}
      />
      Request High Priority
    </label>
    {requestPriority && (
      <div className="training-priority-reason">
        <label className="form-label" htmlFor="training-priority-reason">Reason for priority</label>
        <textarea
          id="training-priority-reason"
          className="form-input"
          value={priorityReason}
          onChange={(e) => setPriorityReason(e.target.value)}
          placeholder="Explain why this training run should be prioritized..."
        />
      </div>
    )}
  </div>
);

interface SubmitRowProps {
  canSubmit: boolean;
  submitting: boolean;
  jobId?: string;
}

const SubmitRow: React.FC<SubmitRowProps> = ({ canSubmit, submitting, jobId }) => (
  <div className="training-submit-row">
    <span className="ws-hint">
      Submitting arms the built image - the image is never rebuilt.
      {jobId && (
        <>
          {' '}
          <Link to={`/builds/${jobId}`} className="training-build-link">
            Inspect build log <ArrowRight size={12} />
          </Link>
        </>
      )}
    </span>
    <button type="submit" className="btn btn-primary" disabled={!canSubmit}>
      {submitting ? <Loader2 size={16} className="animate-spin" /> : <Rocket size={16} />}
      {submitting ? 'Submitting...' : 'Start training'}
    </button>
  </div>
);

export default Training;
