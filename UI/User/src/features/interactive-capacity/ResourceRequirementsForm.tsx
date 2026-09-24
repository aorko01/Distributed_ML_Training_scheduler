import React from 'react';
import type { ResourceRequirements, CapacityOptions } from '../../services/interactive';

interface Props {
  value: ResourceRequirements;
  options: CapacityOptions | null;
  onChange: (next: ResourceRequirements) => void;
  disabled?: boolean;
}

function numberOptions(values: number[], suffix = ''): React.ReactNode {
  return (
    <>
      {values.map((v) => (
        <option key={v} value={v}>{v}{suffix}</option>
      ))}
    </>
  );
}

export const ResourceRequirementsForm: React.FC<Props> = ({ value, options, onChange, disabled }) => {
  const set = (patch: Partial<ResourceRequirements>) => onChange({ ...value, ...patch });
  const bounds = options?.bounds;
  const gpuChoices = options?.gpu_models ?? [];
  return (
    <div className="filter-grid" aria-label="Minimum system requirements">
      <div className="form-group" style={{ marginBottom: 0 }}>
        <label className="form-label" htmlFor="req-gpu-model">GPU model (optional)</label>
        <select
          id="req-gpu-model"
          className="form-select"
          value={value.gpu_model ?? ''}
          disabled={disabled}
          onChange={(e) => set({ gpu_model: e.target.value === '' ? null : e.target.value })}
        >
          <option value="">Any GPU</option>
          {gpuChoices.map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>
      </div>
      <div className="form-group" style={{ marginBottom: 0 }}>
        <label className="form-label" htmlFor="req-vram">GPU VRAM, min (GB)</label>
        <input
          id="req-vram"
          className="form-input"
          type="number"
          min={bounds?.minimum_vram_gb.min ?? 0.5}
          max={bounds?.minimum_vram_gb.max ?? 512}
          step="any"
          value={value.minimum_vram_gb}
          disabled={disabled}
          onChange={(e) => set({ minimum_vram_gb: Number(e.target.value) })}
        />
        {options && options.vram_choices.length > 0 && (
          <select
            aria-label="VRAM presets"
            className="form-select"
            style={{ marginTop: '0.5rem' }}
            value=""
            disabled={disabled}
            onChange={(e) => { if (e.target.value) set({ minimum_vram_gb: Number(e.target.value) }); }}
          >
            <option value="">Preset…</option>
            {numberOptions(options.vram_choices)}
          </select>
        )}
      </div>
      <div className="form-group" style={{ marginBottom: 0 }}>
        <label className="form-label" htmlFor="req-cpu">CPU cores, min</label>
        <input
          id="req-cpu"
          className="form-input"
          type="number"
          min={bounds?.cpu_cores.min ?? 0.5}
          max={bounds?.cpu_cores.max ?? 1024}
          step="any"
          value={value.cpu_cores}
          disabled={disabled}
          onChange={(e) => set({ cpu_cores: Number(e.target.value) })}
        />
      </div>
      <div className="form-group" style={{ marginBottom: 0 }}>
        <label className="form-label" htmlFor="req-ram">RAM, min (GB)</label>
        <input
          id="req-ram"
          className="form-input"
          type="number"
          min={bounds?.memory_gb.min ?? 0.5}
          max={bounds?.memory_gb.max ?? 4096}
          step="any"
          value={value.memory_gb}
          disabled={disabled}
          onChange={(e) => set({ memory_gb: Number(e.target.value) })}
        />
      </div>
      <div className="form-group" style={{ marginBottom: 0 }}>
        <label className="form-label" htmlFor="req-disk">Writable disk, min (GB)</label>
        <input
          id="req-disk"
          className="form-input"
          type="number"
          min={bounds?.disk_gb.min ?? 1}
          max={bounds?.disk_gb.max ?? 1000000}
          step={1}
          value={value.disk_gb}
          disabled={disabled}
          onChange={(e) => set({ disk_gb: Number(e.target.value) })}
        />
      </div>
    </div>
  );
};
