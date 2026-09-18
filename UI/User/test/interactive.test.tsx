// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import SubmitJob from '../src/pages/SubmitJob';
import InteractiveDetails from '../src/pages/InteractiveDetails';
import { interactive, creationRequest } from '../src/services/interactive';
import { fetchPytorchVersions } from '../src/services/docker';
import { submitJob } from '../src/services/jobs';

vi.mock('../src/services/interactive', async importOriginal => {
  const original = await importOriginal<typeof import('../src/services/interactive')>();
  return { ...original, interactive: { bases: vi.fn(), sources: vi.fn(), create: vi.fn(), detail: vi.fn(), logs: vi.fn(), cancel: vi.fn(), runtime: vi.fn(), start: vi.fn(), stop: vi.fn(), connection: vi.fn() } };
});
vi.mock('../src/services/jobs', () => ({ submitJob: vi.fn() }));
vi.mock('../src/services/docker', () => ({ fetchPytorchVersions: vi.fn().mockResolvedValue([{ version: '2.5.1', cudaVersions: [{ cuda: '12.4', cudnn: '9', tag: 'pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime' }] }]) }));
afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(interactive.runtime).mockResolvedValue(null);
  vi.mocked(interactive.bases).mockResolvedValue([{ id: 'pytorch-2.5.1-cuda12.4', label: 'PyTorch CUDA 12.4' }]);
  vi.mocked(interactive.sources).mockResolvedValue([{ id: 'owned-job', name: 'My job' }]);
  vi.mocked(fetchPytorchVersions).mockResolvedValue([{ version: '2.5.1', cudaVersions: [{ cuda: '12.4', cudnn: '9', tag: 'pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime' }] }]);
});
function creation() {
  return render(<MemoryRouter initialEntries={['/submit?mode=interactive']}><Routes>
    <Route path="/jobs/:id" element={<p>Created batch job</p>} /><Route path="/submit" element={<SubmitJob />} /><Route path="/interactive/:id" element={<p>Created workspace</p>} />
  </Routes></MemoryRouter>);
}
function details() {
  return render(<MemoryRouter initialEntries={['/interactive/workspace']}><Routes><Route path="/interactive/:id" element={<InteractiveDetails />} /></Routes></MemoryRouter>);
}
function state(value: 'QUEUED' | 'BUILDING' | 'IMAGE_READY' | 'FAILED' | 'CANCELLED') {
  return { id: 'workspace', name: 'Workspace', source_type: 'UPLOAD', source_job_id: null,
    revision: { id: 'rev', revision_number: 1, origin: 'UPLOAD', state: value, image_tag: null, image_digest_ref: null, failure_reason: value === 'FAILED' ? 'Dependencies failed' : null } };
}

it('switches source and submission mode without interactive scheduling fields', async () => {
  creation();
  await screen.findByRole('option', { name: '2.5.1' });
  await screen.findByRole('option', { name: 'CUDA 12.4 / cuDNN 9' });
  expect(screen.queryByText('Run Command')).toBeNull();
  expect(screen.queryByText('Priority')).toBeNull();
  fireEvent.change(screen.getByLabelText('Workspace source'), { target: { value: 'job' } });
  await screen.findByRole('option', { name: 'My job' });
  expect(screen.queryByLabelText('Workspace ZIP (requirements.txt required, up to 64 MiB)')).toBeNull();
  expect(screen.queryByLabelText('PyTorch Version')).toBeNull();
  expect(screen.queryByLabelText('CUDA / cuDNN Version')).toBeNull();
  fireEvent.change(screen.getByLabelText('Create'), { target: { value: 'batch' } });
  await screen.findByText('Submit New Job');
  expect(screen.queryByLabelText('Workspace source')).toBeNull();
  expect(screen.getByRole('button', { name: 'Submit Job' })).toBeTruthy();
});

it('encodes the exact multipart and JSON request shapes', () => {
  const file = new File(['zip'], 'workspace.zip');
  const upload = creationRequest({ kind: 'upload', name: 'Upload', baseImageId: 'base', file }, 'key');
  expect(upload.path).toBe('/from-upload');
  expect([...((upload.init.body as FormData).keys())]).toEqual(['name', 'base_image_id', 'file']);
  expect(upload.init.headers).toEqual({ 'Idempotency-Key': 'key' });
  const job = creationRequest({ kind: 'job', name: 'Debug', sourceJobId: 'owned-job' }, 'key');
  expect(JSON.parse(job.init.body as string)).toEqual({ name: 'Debug', source_job_id: 'owned-job' });
});

it('submits existing job and prevents double submit', async () => {
  let resolve!: (value: ReturnType<typeof state>) => void;
  vi.mocked(interactive.create).mockImplementation(() => new Promise(done => { resolve = done; }));
  creation();
  fireEvent.change(screen.getByLabelText('Workspace source'), { target: { value: 'job' } });
  await screen.findByRole('option', { name: 'My job' });
  fireEvent.change(screen.getByLabelText('Workspace name'), { target: { value: 'Debug' } });
  const button = screen.getByRole('button', { name: 'Create workspace' });
  fireEvent.click(button); fireEvent.click(button);
  expect(interactive.create).toHaveBeenCalledTimes(1);
  expect(vi.mocked(interactive.create).mock.calls[0][0]).toEqual({ kind: 'job', name: 'Debug', sourceJobId: 'owned-job' });
  resolve(state('QUEUED'));
  await screen.findByText('Created workspace');
});

it('keeps upload idempotency key stable after network failure', async () => {
  vi.mocked(interactive.create).mockRejectedValueOnce(new Error('Network unavailable')).mockResolvedValueOnce(state('QUEUED'));
  creation();
  await screen.findByRole('option', { name: 'CUDA 12.4 / cuDNN 9' });
  fireEvent.change(screen.getByLabelText('Workspace name'), { target: { value: 'Upload' } });
  fireEvent.change(screen.getByLabelText('Workspace ZIP (requirements.txt required, up to 64 MiB)'), { target: { files: [new File(['zip'], 'workspace.zip')] } });
  fireEvent.submit(screen.getByRole('button', { name: 'Create workspace' }).closest('form')!);
  await screen.findByText('Network unavailable');
  fireEvent.submit(screen.getByRole('button', { name: 'Create workspace' }).closest('form')!);
  await screen.findByText('Created workspace');
  const calls = vi.mocked(interactive.create).mock.calls;
  expect(calls[0][1]).toBe(calls[1][1]);
  expect(calls[0][0].kind).toBe('upload');
  expect(calls[0][0]).toEqual({ kind: 'upload', name: 'Upload', baseImageId: 'pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime', file: expect.any(File) });
});

it('renders empty owned-job list and API failure with retry', async () => {
  vi.mocked(interactive.sources).mockResolvedValue([]);
  creation();
  fireEvent.change(screen.getByLabelText('Workspace source'), { target: { value: 'job' } });
  await screen.findByText('No jobs with an available image.');
  expect((screen.getByRole('button', { name: 'Create workspace' }) as HTMLButtonElement).disabled).toBe(true);
  cleanup();
  vi.mocked(fetchPytorchVersions).mockRejectedValueOnce(new Error('Authentication failed')).mockResolvedValueOnce([{ version: '2.5.1', cudaVersions: [{ cuda: '12.4', cudnn: '9', tag: 'pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime' }] }]);
  creation();
  await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button', { name: 'Reload choices' }));
  await screen.findByRole('option', { name: 'CUDA 12.4 / cuDNN 9' });
});

it.each(['QUEUED', 'BUILDING', 'IMAGE_READY', 'FAILED', 'CANCELLED'] as const)('renders %s without enabling runtime actions', async value => {
  vi.mocked(interactive.detail).mockResolvedValue(state(value));
  vi.mocked(interactive.logs).mockResolvedValue({ lines: ['Building workload image'], state: value });
  details();
  await screen.findByRole('heading', { name: 'Workspace' });
  expect(screen.getByText(value === 'IMAGE_READY' ? 'Image ready' : value)).toBeTruthy();
  expect((screen.getByRole('button', { name: 'Connect' }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole('button', { name: 'Save as new revision' }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText(/This runtime is temporary/)).toBeTruthy();
});

it('shows not-found and retries without cached workspace data', async () => {
  vi.mocked(interactive.detail).mockRejectedValueOnce(new Error('Workspace not found')).mockResolvedValueOnce(state('IMAGE_READY'));
  vi.mocked(interactive.logs).mockResolvedValue({ lines: [], state: 'IMAGE_READY' });
  details();
  await screen.findByText('Workspace not found');
  expect(screen.queryByRole('heading', { name: 'Workspace' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await screen.findByRole('heading', { name: 'Workspace' });
});


it('preserves the batch submission request and navigation', async () => {
  vi.mocked(submitJob).mockResolvedValue({ id: 'batch-job' } as Awaited<ReturnType<typeof submitJob>>);
  const view = creation();
  fireEvent.change(screen.getByLabelText('Create'), { target: { value: 'batch' } });
  await screen.findByRole('option', { name: 'CUDA 12.4 / cuDNN 9' });
  fireEvent.change(screen.getByPlaceholderText('e.g. ResNet50_Training'), { target: { value: 'Training' } });
  fireEvent.change(screen.getByPlaceholderText('python train.py'), { target: { value: 'python custom.py' } });
  const archive = new File(['zip'], 'training.zip');
  fireEvent.change(view.container.querySelector('input[type="file"]')!, { target: { files: [archive] } });
  fireEvent.click(screen.getByRole('button', { name: 'Submit Job' }));
  await screen.findByText('Created batch job');
  expect(submitJob).toHaveBeenCalledWith({ name: 'Training', command: 'python custom.py', resumeCommand: undefined,
    pytorchVersion: '2.5.1', cudaVersion: '12.4', dockerBaseImage: 'pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime',
    requestForPriority: false, reasonForPriority: undefined }, archive);
  expect(interactive.create).not.toHaveBeenCalled();
});
