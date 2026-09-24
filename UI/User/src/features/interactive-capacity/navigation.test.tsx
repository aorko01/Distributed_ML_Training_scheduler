import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Layout from '../../components/Layout';
import { workloadLabel } from './requirements';

describe('unified navigation', () => {
  it('sidebar has only the merged interactive entry (no Machines)', () => {
    render(<MemoryRouter><Layout /></MemoryRouter>);
    expect(screen.getByText(/Interactive workspaces/)).toBeDefined();
    expect(screen.queryByText(/^Machines$/)).toBeNull();
  });
  it('machine cards are informational (no select control) and describe exact vs higher consistently', () => {
    expect(workloadLabel('batch_training')).toBe('Batch training');
  });
});
