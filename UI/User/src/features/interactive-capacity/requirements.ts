import type { ResourceRequirements, CapacityOptions } from '../../services/interactive';

export const DEFAULT_REQUIREMENTS: ResourceRequirements = {
  gpu_model: null,
  minimum_vram_gb: 4,
  cpu_cores: 2,
  memory_gb: 8,
  disk_gb: 20,
};

export function normalizeRequirements(value: Partial<ResourceRequirements> | null | undefined, fallback?: ResourceRequirements | null): ResourceRequirements {
  const base = fallback ?? DEFAULT_REQUIREMENTS;
  const pick = (v: unknown, fb: number): number => {
    const n = typeof v === 'string' && v !== '' ? Number(v) : (v as number);
    return typeof n === 'number' && Number.isFinite(n) && n > 0 ? n : fb;
  };
  const gpu = (value?.gpu_model ?? base.gpu_model ?? null) as string | null;
  return {
    gpu_model: gpu && gpu.trim() !== '' ? gpu : null,
    minimum_vram_gb: pick(value?.minimum_vram_gb, base.minimum_vram_gb),
    cpu_cores: pick(value?.cpu_cores, base.cpu_cores),
    memory_gb: pick(value?.memory_gb, base.memory_gb),
    disk_gb: Math.round(pick(value?.disk_gb, base.disk_gb)),
  };
}

export function requirementsValid(value: ResourceRequirements, options?: CapacityOptions | null): boolean {
  if (!Number.isFinite(value.minimum_vram_gb) || value.minimum_vram_gb <= 0) return false;
  if (!Number.isFinite(value.cpu_cores) || value.cpu_cores <= 0) return false;
  if (!Number.isFinite(value.memory_gb) || value.memory_gb <= 0) return false;
  if (!Number.isFinite(value.disk_gb) || value.disk_gb <= 0) return false;
  if (options) {
    const b = options.bounds;
    if (value.cpu_cores < b.cpu_cores.min || value.cpu_cores > b.cpu_cores.max) return false;
    if (value.memory_gb < b.memory_gb.min || value.memory_gb > b.memory_gb.max) return false;
    if (value.disk_gb < b.disk_gb.min || value.disk_gb > b.disk_gb.max) return false;
    if (value.minimum_vram_gb < b.minimum_vram_gb.min || value.minimum_vram_gb > b.minimum_vram_gb.max) return false;
    if (value.gpu_model && b.gpu_models_allowlist.length > 0 && !b.gpu_models_allowlist.includes(value.gpu_model)) return false;
  }
  return true;
}

export function workloadLabel(kind: string): string {
  if (kind === 'batch_training') return 'Batch training';
  if (kind === 'vram_estimation') return 'VRAM estimation';
  return 'Interactive workspace';
}
