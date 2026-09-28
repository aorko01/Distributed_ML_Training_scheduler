import { beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { WorkspaceHandoff } from './WorkspaceHandoff';
import { interactive, InteractiveRequestError, type Runtime } from '../../services/interactive';

vi.mock('../../services/interactive', () => ({ InteractiveRequestError: class extends Error {
  readonly status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}, interactive: {
  saveWorkspace: vi.fn(), saveStatus: vi.fn(), submitTraining: vi.fn(), trainingStatus: vi.fn(),
} }));

const runtime: Runtime = {
  id: 'runtime-1', workspace_id: 'workspace-1', revision_id: 'revision-1', generation: 2,
  profile_version: 'v1', state: 'READY', desired_state: 'RUNNING', failure_detail: null,
  lifetime_deadline: null, editor_capable: true, ssh_capable: true, ssh_ready: true,
  save_enabled: true, training_submission_enabled: true,
};

beforeEach(() => {
  cleanup();
  sessionStorage.clear();
  vi.clearAllMocks();
});

describe('VS Code Remote save and training handoff', () => {
  it('saves the running container without opening the browser editor', async () => {
    vi.mocked(interactive.saveWorkspace).mockResolvedValue({
      id: 'save-1', workspace_id: 'workspace-1', runtime_id: runtime.id, generation: 2,
      state: 'REQUESTED', target_revision_id: null, failure_code: null, failure_detail: null,
    });
    vi.mocked(interactive.saveStatus).mockResolvedValue({
      id: 'save-1', workspace_id: 'workspace-1', runtime_id: runtime.id, generation: 2,
      state: 'SUCCEEDED', target_revision_id: 'revision-2', failure_code: null, failure_detail: null,
    });
    render(<MemoryRouter><WorkspaceHandoff runtime={runtime} workspaceName="My workspace" onActiveChange={() => {}} /></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', { name: 'Save for Later' }));
    await waitFor(() => expect(interactive.saveWorkspace).toHaveBeenCalledWith(runtime.id, expect.any(String), 2, 'revision-1'));
    expect(await screen.findByText(/revision saved/i)).toBeDefined();
  });

  it('submits the container image and keeps the job link after the runtime stops', async () => {
    vi.mocked(interactive.submitTraining).mockResolvedValue({
      id: 'submission-1', workspace_id: 'workspace-1', runtime_id: runtime.id,
      state: 'SAVING', job_id: null, failure_code: null, failure_detail: null,
    });
    vi.mocked(interactive.trainingStatus).mockResolvedValue({
      id: 'submission-1', workspace_id: 'workspace-1', runtime_id: runtime.id,
      state: 'JOB_CREATED', job_id: 'job-1', failure_code: null, failure_detail: null,
    });
    const view = render(<MemoryRouter><WorkspaceHandoff runtime={runtime} workspaceName="My workspace" onActiveChange={() => {}} /></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', { name: 'Submit for Training' }));
    expect(screen.getByText(/Save all files in VS Code before continuing/)).toBeDefined();
    fireEvent.change(screen.getByLabelText('Training script command'), { target: { value: 'python train.py --epochs 10' } });
    fireEvent.change(screen.getByLabelText('Resume script command (optional)'), { target: { value: 'python resume.py' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save image and start training' }));
    await waitFor(() => expect(interactive.submitTraining).toHaveBeenCalledWith(runtime.id, expect.any(String), 2, 'revision-1', {
      name: 'My workspace training', command: 'python train.py --epochs 10', resume_command: 'python resume.py',
    }));
    expect(await screen.findByRole('link', { name: 'View job' })).toHaveProperty('href', 'http://localhost:3000/jobs/job-1');
    view.unmount();
    render(<MemoryRouter><WorkspaceHandoff runtime={{ ...runtime, state: 'STOPPED', desired_state: 'STOPPED' }} workspaceName="My workspace" onActiveChange={() => {}} /></MemoryRouter>);
    expect(await screen.findByRole('link', { name: 'View job' })).toBeDefined();
    expect(interactive.submitTraining).toHaveBeenCalledTimes(1);
  });

  it('disables saving when the backend did not enable it for this runtime', () => {
    render(<MemoryRouter><WorkspaceHandoff runtime={{ ...runtime, save_enabled: false }} workspaceName="My workspace" onActiveChange={() => {}} /></MemoryRouter>);
    expect((screen.getByRole('button', { name: 'Save for Later' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Submit for Training' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('retries an uncertain submission with the same key after the runtime stops', async () => {
    vi.mocked(interactive.submitTraining)
      .mockRejectedValueOnce(new TypeError('Network unavailable'))
      .mockResolvedValueOnce({ id: 'submission-2', workspace_id: 'workspace-1', runtime_id: runtime.id,
        state: 'SAVING', job_id: null, failure_code: null, failure_detail: null });
    vi.mocked(interactive.trainingStatus).mockResolvedValue({ id: 'submission-2', workspace_id: 'workspace-1', runtime_id: runtime.id,
      state: 'JOB_CREATED', job_id: 'job-2', failure_code: null, failure_detail: null });
    const first = render(<MemoryRouter><WorkspaceHandoff runtime={runtime} workspaceName="My workspace" onActiveChange={() => {}} /></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', { name: 'Submit for Training' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save image and start training' }));
    expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'Network unavailable');
    first.unmount();

    render(<MemoryRouter><WorkspaceHandoff runtime={{ ...runtime, state: 'STOPPED', desired_state: 'STOPPED' }} workspaceName="My workspace" onActiveChange={() => {}} /></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', { name: 'Retry training submission' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save image and start training' }));
    expect(await screen.findByRole('link', { name: 'View job' })).toBeDefined();
    const calls = vi.mocked(interactive.submitTraining).mock.calls;
    expect(calls).toHaveLength(2);
    expect(calls[1][1]).toBe(calls[0][1]);
    expect(calls[1][4]).toEqual(calls[0][4]);
  });

  it('allows corrected settings after a validation rejection', async () => {
    vi.mocked(interactive.submitTraining)
      .mockRejectedValueOnce(new InteractiveRequestError('Invalid training command', 422))
      .mockResolvedValueOnce({ id: 'submission-3', workspace_id: 'workspace-1', runtime_id: runtime.id,
        state: 'SAVING', job_id: null, failure_code: null, failure_detail: null });
    vi.mocked(interactive.trainingStatus).mockResolvedValue({ id: 'submission-3', workspace_id: 'workspace-1', runtime_id: runtime.id,
      state: 'JOB_CREATED', job_id: 'job-3', failure_code: null, failure_detail: null });
    render(<MemoryRouter><WorkspaceHandoff runtime={runtime} workspaceName="My workspace" onActiveChange={() => {}} /></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', { name: 'Submit for Training' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save image and start training' }));
    expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'Invalid training command');
    fireEvent.change(screen.getByLabelText('Training script command'), { target: { value: 'python fixed.py' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save image and start training' }));
    expect(await screen.findByRole('link', { name: 'View job' })).toBeDefined();
    const calls = vi.mocked(interactive.submitTraining).mock.calls;
    expect(calls[1][1]).not.toBe(calls[0][1]);
    expect(calls[1][4].command).toBe('python fixed.py');
  });
});
