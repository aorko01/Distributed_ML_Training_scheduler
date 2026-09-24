import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ResourceRequirementsForm } from './ResourceRequirementsForm';
import { CapacitySummary } from './CapacitySummary';
import { MachineCard, MachineGrid } from './MachineCard';
import { normalizeRequirements, requirementsValid, workloadLabel } from './requirements';
import { creationRequest } from '../../services/interactive';
import type { CapacityMachine, CapacityPreview } from '../../services/interactive';

const machine = (over: Partial<CapacityMachine> = {}): CapacityMachine => ({
  machine_key: 'abc123',
  display_name: 'gpu-worker-02',
  gpu_model: 'NVIDIA A100-SXM4-40GB',
  gpu_count: 2,
  total_vram_gb: 40,
  free_vram_gb: 38,
  cpu_cores: 32,
  cpu_load_percent: 22,
  total_ram_gb: 128,
  free_ram_gb: 96,
  total_disk_gb: 1000,
  free_disk_gb: 600,
  gpu_load_percent: 10,
  available_now: true,
  availability_reason: null,
  workloads: [],
  ...over,
});

describe('requirements helpers', () => {
  it('normalizes empty gpu model to null and rounds disk', () => {
    const out = normalizeRequirements({ gpu_model: '', disk_gb: 50.7 } as never);
    expect(out.gpu_model).toBeNull();
    expect(out.disk_gb).toBe(51);
  });
  it('validates against operator bounds', () => {
    const options = {
      defaults: { gpu_model: null, minimum_vram_gb: 4, cpu_cores: 2, memory_gb: 8, disk_gb: 20 },
      bounds: {
        cpu_cores: { min: 0.5, max: 64, default: 2 },
        memory_gb: { min: 0.5, max: 512, default: 8 },
        disk_gb: { min: 1, max: 2000, default: 20 },
        minimum_vram_gb: { min: 0.5, max: 192, default: 4 },
        gpu_models_allowlist: [],
      },
      gpu_models: [], cpu_choices: [], ram_choices: [], vram_choices: [], disk_choices: [],
    };
    expect(requirementsValid({ gpu_model: null, minimum_vram_gb: 4, cpu_cores: 2, memory_gb: 8, disk_gb: 20 }, options)).toBe(true);
    expect(requirementsValid({ gpu_model: null, minimum_vram_gb: 4, cpu_cores: 1000, memory_gb: 8, disk_gb: 20 }, options)).toBe(false);
  });
  it('labels workload kinds', () => {
    expect(workloadLabel('batch_training')).toBe('Batch training');
    expect(workloadLabel('vram_estimation')).toBe('VRAM estimation');
    expect(workloadLabel('interactive_access')).toBe('Interactive workspace');
  });
});

describe('ResourceRequirementsForm', () => {
  it('shows editable base selection only via requirements, and any-gpu option', () => {
    const onChange = vi.fn();
    render(<ResourceRequirementsForm value={{ gpu_model: null, minimum_vram_gb: 16, cpu_cores: 4, memory_gb: 16, disk_gb: 50 }} options={null} onChange={onChange} />);
    expect(screen.getByLabelText(/GPU model/i)).toBeDefined();
    fireEvent.change(screen.getByLabelText(/GPU VRAM/i), { target: { value: '24' } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ minimum_vram_gb: 24 }));
  });
});

describe('MachineCard', () => {
  it('renders available card with load and no select control', () => {
    render(<MachineCard machine={machine()} />);
    expect(screen.getByText('gpu-worker-02')).toBeDefined();
    expect(screen.getAllByText('Available now').length).toBeGreaterThan(0);
    expect(screen.queryByRole('radio')).toBeNull();
    expect(screen.queryByRole('checkbox')).toBeNull();
  });
  it('busy cards show workload type', () => {
    render(<MachineCard machine={machine({ available_now: false, availability_reason: 'scheduler_assignments_active', workloads: [{ kind: 'batch_training', state: 'ACTIVE', mine: false }] })} />);
    expect(screen.getByText('Busy')).toBeDefined();
    expect(screen.getByText(/Batch training/)).toBeDefined();
  });
  it('uses cpu load for cpu bar, not mem', () => {
    const m = machine({ cpu_load_percent: 22, gpu_load_percent: 10 });
    render(<MachineCard machine={m} />);
    expect(screen.getAllByText(/32 cores/).length).toBeGreaterThan(0);
  });
});

describe('MachineGrid', () => {
  it('only matching online cards render (caller filters); empty shows no-match', () => {
    render(<MachineGrid machines={[]} />);
    expect(screen.getByText(/No online machines match/i)).toBeDefined();
  });
});

describe('CapacitySummary', () => {
  it('shows matching/available/busy/queued counts', () => {
    const preview = {
      generated_at: new Date().toISOString(),
      requirements: { gpu_model: null, minimum_vram_gb: 4, cpu_cores: 2, memory_gb: 8, disk_gb: 20 },
      matching_online: 2, available_now: 1, busy: 1, queued_interactive_requests: 3, machines: [],
    } as CapacityPreview;
    render(<CapacitySummary preview={preview} loading={false} />);
    expect(screen.getByText('Matching online')).toBeDefined();
    expect(screen.getAllByText('Available now').length).toBeGreaterThan(0);
  });
});

describe('creationRequest', () => {
  it('sends requirements but no machine identity', () => {
    const { init } = creationRequest({ kind: 'job', name: 'n', sourceJobId: 'j', requirements: { gpu_model: null, minimum_vram_gb: 4, cpu_cores: 2, memory_gb: 8, disk_gb: 20 } }, 'k');
    const body = JSON.parse((init as { body: string }).body as string);
    expect(body.requirements.minimum_vram_gb).toBe(4);
    expect(body.worker_id).toBeUndefined();
    expect(body.hostname).toBeUndefined();
    expect(JSON.stringify(body)).not.toContain('GPU-');
  });
  it('upload form carries bounded requirements JSON', () => {
    const file = new File(['x'], 'w.zip', { type: 'application/zip' });
    const { init } = creationRequest({ kind: 'upload', name: 'n', baseImageId: 'tag', file, requirements: { gpu_model: null, minimum_vram_gb: 4, cpu_cores: 2, memory_gb: 8, disk_gb: 20 } }, 'k');
    const form = (init as { body: FormData }).body as FormData;
    expect(form.get('requirements')).toContain('minimum_vram_gb');
    expect(form.get('worker_id' as string)).toBeNull();
  });
});
