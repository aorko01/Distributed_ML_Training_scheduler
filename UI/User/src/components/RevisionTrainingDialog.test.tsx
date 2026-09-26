import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { interactive } from '../services/interactive';
import { RevisionTrainingDialog } from './RevisionTrainingDialog';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('submits the selected built image as a new job and opens its details', async () => {
  const submit = vi.spyOn(interactive, 'submitRevisionTraining').mockResolvedValue({ job_id: 'batch-123', status: 'VRAM_ESTIMATION_PENDING' });
  const user = userEvent.setup();
  render(
    <MemoryRouter initialEntries={['/interactive/workspace-1/editor']}>
      <Routes>
        <Route path="/interactive/:id/editor" element={<RevisionTrainingDialog workspaceId="workspace-1" workspaceName="Session" revisionId="revision-1" onClose={() => {}} />} />
        <Route path="/jobs/:id" element={<p>Batch job details</p>} />
      </Routes>
    </MemoryRouter>,
  );
  await user.clear(screen.getByLabelText('New job name'));
  await user.type(screen.getByLabelText('New job name'), 'Fresh batch');
  await user.type(screen.getByLabelText('Entry script'), 'python train.py');
  await user.type(screen.getByLabelText('Retry script (optional)'), 'python resume.py');
  await user.click(screen.getByRole('button', { name: 'Submit for training' }));
  expect(submit).toHaveBeenCalledOnce();
  expect(submit).toHaveBeenCalledWith('workspace-1', expect.any(String), {
    name: 'Fresh batch', command: 'python train.py', resume_command: 'python resume.py',
    revision_id: 'revision-1',
  });
  expect(await screen.findByText('Batch job details')).toBeTruthy();
});
