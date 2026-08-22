import React, { useEffect, useMemo, useState } from 'react';
import {
  Server,
  Gpu,
  HardDrive,
  MemoryStick,
  Cpu,
  Activity,
  Loader2,
  Zap,
  Filter,
  RotateCcw,
  CalendarClock,
  CheckCircle2,
  Hash,
  Layers,
} from 'lucide-react';
import {
  fetchAllNodes,
  fetchResourceOptions,
  fetchResourceSummary,
  createResourceRequest,
  type WorkerNode,
  type ResourceOptions,
  type ResourceSummary,
  type ResourceConfig,
  type ResourceRequestPayload,
} from '../services/workers';

type Op = 'ge' | 'eq';

interface ConfigState {
  op: Op;
  gpuType: string;
  gpuVram: number | null;
  cpuRam: number | null;
  cpuCores: number | null;
  disk: number | null;
}

const EMPTY_CONFIG: ConfigState = { op: 'ge', gpuType: '', gpuVram: null, cpuRam: null, cpuCores: null, disk: null };

const opLabel = (op: Op) => (op === 'ge' ? '≥ (at least)' : '= (equal to)');

const statusBadge = (status: WorkerNode['status']) => (
  <span className={`badge ${status === 'online' ? 'badge-online' : 'badge-offline'}`}>
    <span className="status-dot" />
    {status}
  </span>
);

interface ResourceBarProps {
  label: string;
  icon: React.ReactNode;
  usedPercent: number;
  details: string;
}

const ResourceBar: React.FC<ResourceBarProps> = ({ label, icon, usedPercent, details }) => {
  const clamped = Math.min(100, Math.max(0, usedPercent));
  const level = clamped >= 85 ? 'high' : clamped >= 60 ? 'mid' : 'low';
  return (
    <div className="resource-row">
      <div className="resource-head">
        <span className="resource-label">{icon}{label}</span>
        <span className="resource-value">{details}</span>
      </div>
      <div className="resource-bar">
        <div className={`resource-bar-fill ${level}`} style={{ width: `${clamped}%` }} />
      </div>
    </div>
  );
};

const numMatch = (actual: number, required: number | null, op: Op): boolean => {
  if (required === null) return true;
  return op === 'eq' ? actual === required : actual >= required;
};

const Machines: React.FC = () => {
  const [nodes, setNodes] = useState<WorkerNode[]>([]);
  const [options, setOptions] = useState<ResourceOptions | null>(null);
  const [summary, setSummary] = useState<ResourceSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [config, setConfig] = useState<ConfigState>(EMPTY_CONFIG);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [acquiredNote, setAcquiredNote] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const loadData = async () => {
      try {
        const [nodeData, optionData] = await Promise.all([fetchAllNodes(), fetchResourceOptions()]);
        if (!cancelled) {
          setNodes(nodeData);
          setOptions(optionData);
        }
      } catch (e) {
        if (!cancelled) setLoadError(e instanceof Error ? e.message : 'Failed to load machines.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    loadData();
    return () => {
      cancelled = false;
    };
  }, []);

  const activeCount = useMemo(
    () =>
      (config.gpuType ? 1 : 0) +
      (config.gpuVram !== null ? 1 : 0) +
      (config.cpuRam !== null ? 1 : 0) +
      (config.cpuCores !== null ? 1 : 0) +
      (config.disk !== null ? 1 : 0),
    [config],
  );

  const hasActiveFilters = activeCount > 0;

  const payloadConfig = useMemo((): ResourceConfig => {
    const payload: ResourceConfig = { op: config.op };
    if (config.gpuType) payload.gpu_type = config.gpuType;
    if (config.gpuVram !== null) payload.gpu_vram = config.gpuVram;
    if (config.cpuRam !== null) payload.cpu_ram = config.cpuRam;
    if (config.cpuCores !== null) payload.cpu_cores = config.cpuCores;
    if (config.disk !== null) payload.disk = config.disk;
    return payload;
  }, [config]);

  useEffect(() => {
    let cancelled = false;
    const loadSummary = async () => {
      try {
        const data = await fetchResourceSummary(payloadConfig);
        if (!cancelled) setSummary(data);
      } catch {
        if (!cancelled) setSummary(null);
      }
    };
    loadSummary();
    return () => {
      cancelled = false;
    };
  }, [payloadConfig]);

  const visibleNodes = useMemo(
    () =>
      nodes.filter(
        (node) =>
          (!config.gpuType || node.gpu_type === config.gpuType) &&
          numMatch(node.total_vram, config.gpuVram, config.op) &&
          numMatch(node.total_ram, config.cpuRam, config.op) &&
          numMatch(node.cpu_cores, config.cpuCores, config.op) &&
          numMatch(node.available_disk, config.disk, config.op),
      ),
    [nodes, config],
  );

  const updateConfig = (patch: Partial<ConfigState>) =>
    setConfig((prev) => ({ ...prev, ...patch }));

  const resetConfig = () => {
    setConfig(EMPTY_CONFIG);
    setSubmitError(null);
    setAcquiredNote(null);
  };

  const diskUsedPercent = (node: WorkerNode) =>
    node.total_disk > 0 ? ((node.total_disk - node.available_disk) / node.total_disk) * 100 : 0;

  const vramUsedPercent = (node: WorkerNode) =>
    node.total_vram > 0 ? ((node.total_vram - node.available_vram) / node.total_vram) * 100 : 0;

  const handleAcquire = async () => {
    setSubmitting(true);
    setSubmitError(null);
    setAcquiredNote(null);
    const payload: ResourceRequestPayload = {};
    if (config.gpuType) payload.gpu_type = config.gpuType;
    if (config.gpuVram !== null) payload.gpu_vram = config.gpuVram;
    if (config.cpuRam !== null) payload.cpu_ram = config.cpuRam;
    if (config.cpuCores !== null) payload.cpu_cores = config.cpuCores;
    if (config.disk !== null) payload.disk = config.disk;
    try {
      const res = await createResourceRequest(payload);
      setAcquiredNote(res.message);
      setSummary((prev) => (prev ? { ...prev, queue_open: res.queue_open, queue_total: prev.queue_total + 1 } : prev));
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : 'Failed to submit resource request.');
    } finally {
      setSubmitting(false);
    }
  };

  const renderOptions = (values: number[], suffix = '') => (
    <>
      <option value="">Any</option>
      {values.map((v) => (
        <option key={v} value={v}>{v}{suffix}</option>
      ))}
    </>
  );

  const numericValue = (v: number | null) => (v === null ? '' : String(v));

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
        <Loader2 className="animate-spin text-blue-500" size={32} />
      </div>
    );
  }

  return (
    <div className="fade-in">
      <h1>Acquire Machines</h1>

      <div className="card demo-banner">
        <Zap size={18} color="var(--status-pending)" />
        <p style={{ margin: 0 }}>
          Select the resource requirements you need (values come from live registered workers), then
          acquire a matching machine. The queue shows how many similar requests are already waiting.
        </p>
      </div>

      {loadError && (
        <div className="card demo-banner" style={{ borderColor: 'rgba(239, 68, 68, 0.4)' }}>
          <p className="error-text" style={{ margin: 0 }}>Failed to load data: {loadError}</p>
        </div>
      )}

      <div className="card" style={{ marginBottom: '1.5rem' }}>
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: '1rem',
          }}
        >
          <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <Filter size={18} color="var(--text-secondary)" />
            Resource Requirements
          </h3>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <label className="form-label" style={{ margin: 0 }}>Compare:</label>
            <select
              className="form-select"
              style={{ width: 'auto', padding: '0.4rem 0.75rem' }}
              value={config.op}
              onChange={(e) => updateConfig({ op: e.target.value as Op })}
            >
              <option value="ge">Greater than or equal (≥)</option>
              <option value="eq">Equal (=)</option>
            </select>
            {hasActiveFilters && (
              <button
                className="btn btn-secondary"
                onClick={resetConfig}
                style={{ padding: '0.4rem 0.9rem' }}
              >
                <RotateCcw size={14} /> Clear
              </button>
            )}
          </div>
        </div>

        <div className="filter-grid">
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label className="form-label">GPU Type</label>
            <select
              className="form-select"
              value={config.gpuType}
              onChange={(e) => updateConfig({ gpuType: e.target.value })}
            >
              <option value="">Any</option>
              {(options?.gpu_types ?? []).map((type) => (
                <option key={type} value={type}>{type}</option>
              ))}
            </select>
          </div>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label className="form-label">GPU VRAM (GB)</label>
            <select
              className="form-select"
              value={numericValue(config.gpuVram)}
              onChange={(e) => updateConfig({ gpuVram: e.target.value ? Number(e.target.value) : null })}
            >
              {renderOptions(options?.vram_options ?? [])}
            </select>
          </div>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label className="form-label">CPU Cores</label>
            <select
              className="form-select"
              value={numericValue(config.cpuCores)}
              onChange={(e) => updateConfig({ cpuCores: e.target.value ? Number(e.target.value) : null })}
            >
              {renderOptions(options?.core_options ?? [])}
            </select>
          </div>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label className="form-label">CPU RAM (GB)</label>
            <select
              className="form-select"
              value={numericValue(config.cpuRam)}
              onChange={(e) => updateConfig({ cpuRam: e.target.value ? Number(e.target.value) : null })}
            >
              {renderOptions(options?.ram_options ?? [])}
            </select>
          </div>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label className="form-label">Free Disk (GB)</label>
            <select
              className="form-select"
              value={numericValue(config.disk)}
              onChange={(e) => updateConfig({ disk: e.target.value ? Number(e.target.value) : null })}
            >
              {renderOptions(options?.disk_options ?? [])}
            </select>
          </div>
        </div>

        <div className="config-summary-grid" style={{ marginTop: '1.25rem' }}>
          <div className="config-summary-item">
            <Layers size={16} color="var(--accent-primary)" />
            <div>
              <div className="config-summary-value">{summary?.matching_nodes ?? '–'}</div>
              <div className="config-summary-label">Matching Machines</div>
            </div>
          </div>
          <div className="config-summary-item">
            <Activity size={16} color="var(--accent-primary)" />
            <div>
              <div className="config-summary-value">{summary?.avg_running_jobs ?? '–'}</div>
              <div className="config-summary-label">Avg Running Jobs / Machine</div>
            </div>
          </div>
          <div className="config-summary-item">
            <Hash size={16} color="var(--accent-primary)" />
            <div>
              <div className="config-summary-value">{summary ? `${summary.queue_open} / ${summary.queue_total}` : '–'}</div>
              <div className="config-summary-label">Queue (open / total)</div>
            </div>
          </div>
          <div className="config-summary-item">
            <MemoryStick size={16} color="var(--accent-primary)" />
            <div>
              <div className="config-summary-value">{visibleNodes.reduce((s, n) => s + n.running_jobs, 0)}</div>
              <div className="config-summary-label">Jobs on Matching Machines</div>
            </div>
          </div>
        </div>

        {acquiredNote && (
          <div className="acquire-success" style={{ marginTop: '1rem' }}>
            <CheckCircle2 size={20} color="var(--status-success)" />
            <p>{acquiredNote}</p>
          </div>
        )}
        {submitError && <p className="error-text" style={{ marginTop: '1rem' }}>{submitError}</p>}

        <div className="config-acquire-bar">
          <div className="config-acquire-info">
            {hasActiveFilters ? (
              <>
                Comparing with <strong>{opLabel(config.op)}</strong> ·{" "}
                {summary?.matching_nodes ?? 0} machine{summary?.matching_nodes === 1 ? '' : 's'} match
              </>
            ) : (
              <>All registered machines are shown. Pick a requirement to narrow the match.</>
            )}
          </div>
          {hasActiveFilters && (
            <button
              className="btn btn-primary"
              onClick={handleAcquire}
              disabled={submitting || !options}
            >
              {submitting ? <Loader2 className="animate-spin" size={18} /> : <CalendarClock size={18} />}
              {submitting ? 'Requesting...' : 'Acquire Matching Machine'}
            </button>
          )}
        </div>
      </div>

      {!loading && !loadError && nodes.length === 0 && (
        <div className="card" style={{ textAlign: 'center', padding: '3rem 1.5rem' }}>
          <Server size={32} color="var(--text-secondary)" style={{ marginBottom: '0.75rem' }} />
          <h3 style={{ marginBottom: '0.5rem' }}>No Machines Registered</h3>
          <p style={{ margin: 0 }}>
            No workers are currently registered with the scheduler. Register a worker to see it here.
          </p>
        </div>
      )}

      <h2 style={{ marginBottom: '1rem' }}>
        Worker Nodes
        {hasActiveFilters && <span className="metric-sub"> ({visibleNodes.length} matching)</span>}
      </h2>

      <div className="machine-grid">
        {visibleNodes.map((node) => (
          <div key={node.worker_id} className={`machine-card ${node.status === 'offline' ? 'machine-offline' : ''}`}>
            <div className="machine-card-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                <div className="machine-icon"><Server size={20} /></div>
                <div>
                  <div className="machine-hostname">{node.hostname}</div>
                  <div className="machine-ip">{node.ip_address}</div>
                </div>
              </div>
              {statusBadge(node.status)}
            </div>

            {node.status === 'offline' && (
              <div className="offline-notice">
                <Activity size={14} />
                This machine is offline and not accepting jobs.
              </div>
            )}

            <div className="machine-specs">
              <span><Gpu size={14} /> {node.gpu_type} x {node.num_gpus}</span>
              <span><Activity size={14} /> {node.gpus_in_use}/{node.num_gpus} GPUs in use</span>
            </div>

            <div className="resource-list">
              <ResourceBar
                label="VRAM"
                icon={<MemoryStick size={14} />}
                usedPercent={vramUsedPercent(node)}
                details={`${node.available_vram.toFixed(0)} / ${node.total_vram.toFixed(0)} GB free`}
              />
              <ResourceBar
                label="GPU Load"
                icon={<Gpu size={14} />}
                usedPercent={node.gpu_load}
                details={`${node.gpu_load.toFixed(0)}%`}
              />
              <ResourceBar
                label="CPU Cores"
                icon={<Cpu size={14} />}
                usedPercent={node.mem_usage}
                details={`${node.cpu_cores} cores`}
              />
              <ResourceBar
                label="RAM"
                icon={<MemoryStick size={14} />}
                usedPercent={node.mem_usage}
                details={`${node.mem_usage.toFixed(0)}% used`}
              />
              <ResourceBar
                label="Disk"
                icon={<HardDrive size={14} />}
                usedPercent={diskUsedPercent(node)}
                details={`${node.available_disk.toFixed(0)} GB free`}
              />
            </div>

            <div className="machine-footer">
              <span style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', color: 'var(--text-secondary)', fontSize: '0.875rem' }}>
                <Activity size={14} /> {node.running_jobs} running {node.running_jobs === 1 ? 'job' : 'jobs'}
              </span>
            </div>
          </div>
        ))}
        {visibleNodes.length === 0 && !loadError && (
          <div className="card" style={{ gridColumn: '1 / -1', textAlign: 'center', padding: '2rem 1rem' }}>
            <p className="error-text" style={{ margin: 0 }}>
              No machines match the selected resource requirements.
            </p>
          </div>
        )}
      </div>
    </div>
  );
};

export default Machines;