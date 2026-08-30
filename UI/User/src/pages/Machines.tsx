import React, { useEffect, useMemo, useState } from 'react';
import {
  Terminal,
  Loader2,
  CheckCircle2,
  RotateCcw,
  Box,
  Upload,
  AlertTriangle,
  RefreshCw,
} from 'lucide-react';
import {
  fetchJobs,
  submitInteractiveSession,
  submitInteractiveDirect,
  type Job,
  type InteractiveSession,
} from '../services/jobs';
import { fetchPytorchVersions, type PytorchVersion, type CudaVariant } from '../services/docker';
import {
  fetchAllNodes,
  fetchResourceOptions,
  fetchResourceSummary,
  type ResourceOptions,
  type ResourceConfig,
  type ResourceSummary,
  type WorkerNode,
} from '../services/workers';

const PYTHON_VERSIONS = ['3.9', '3.10', '3.11', '3.12', '3.13'];

type Mode = 'existing' | 'direct';

const Interactive: React.FC = () => {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [mode, setMode] = useState<Mode>('existing');

  // Existing-job mode
  const [selectedJobId, setSelectedJobId] = useState<string>('');

  // Direct-upload mode
  const [zipFile, setZipFile] = useState<File | null>(null);
  const [pythonVersion, setPythonVersion] = useState('3.11');
  const [versions, setVersions] = useState<PytorchVersion[]>([]);
  const [loadingVersions, setLoadingVersions] = useState(true);
  const [versionError, setVersionError] = useState('');
  const [selectedPyTorch, setSelectedPyTorch] = useState('');
  const [selectedCuda, setSelectedCuda] = useState<CudaVariant | null>(null);
  const [customBaseImage, setCustomBaseImage] = useState('');

  const [sessionName, setSessionName] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [session, setSession] = useState<InteractiveSession | null>(null);

  // System resource selection for interactive sessions
  const [resourceOptions, setResourceOptions] = useState<ResourceOptions | null>(null);
  const [loadingResources, setLoadingResources] = useState(true);
  const [resourceOptionsError, setResourceOptionsError] = useState<string | null>(null);
  const [allNodes, setAllNodes] = useState<WorkerNode[]>([]);
  const [nodesError, setNodesError] = useState<string | null>(null);
  const [selectedResources, setSelectedResources] = useState<ResourceConfig>({ op: 'ge' });
  const [resourceSummary, setResourceSummary] = useState<ResourceSummary | null>(null);
  const [loadingSummary, setLoadingSummary] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const loadJobs = async () => {
      try {
        // Only jobs whose image has been built (not NOT_RUNNABLE) can be used
        const data = await fetchJobs(true);
        if (!cancelled) setJobs(data);
      } catch (e) {
        if (!cancelled) setLoadError(e instanceof Error ? e.message : 'Failed to load your jobs.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    loadJobs();
    return () => {
      cancelled = false;
    };
  }, []);

  useEffect(() => {
    const loadVersions = async () => {
      setLoadingVersions(true);
      setVersionError('');
      try {
        // Same live Docker Hub listing the SubmitJob page uses.
        const data = await fetchPytorchVersions();
        setVersions(data);
      } catch (err) {
        setVersionError(err instanceof Error ? err.message : 'Failed to load PyTorch versions.');
      } finally {
        setLoadingVersions(false);
      }
    };
    loadVersions();
   }, []);

  useEffect(() => {
    let cancelled = false;
    const loadResourcesAndNodes = async () => {
      setLoadingResources(true);
      setResourceOptionsError(null);
      try {
        const options = await fetchResourceOptions();
        if (!cancelled) setResourceOptions(options);
      } catch (e) {
        if (!cancelled) setResourceOptionsError(e instanceof Error ? e.message : 'Failed to load resource options.');
      } finally {
        if (!cancelled) setLoadingResources(false);
      }

      setNodesError(null);
      try {
        const nodes = await fetchAllNodes();
        if (!cancelled) setAllNodes(nodes);
      } catch (e) {
        if (!cancelled) setNodesError(e instanceof Error ? e.message : 'Failed to load machines.');
      }
    };
    loadResourcesAndNodes();
    return () => {
      cancelled = true;
    };
  }, []);

  const selectedJob = jobs.find((j) => j.id === selectedJobId);

  const availableCudas = versions.find((v) => v.version === selectedPyTorch)?.cudaVersions ?? [];

  const handlePyTorchChange = (pt: string) => {
    setSelectedPyTorch(pt);
    setSession(null);
    setSubmitError(null);
    if (!pt) {
      setSelectedCuda(null);
      return;
    }
    const versionData = versions.find((v) => v.version === pt);
    setSelectedCuda(versionData && versionData.cudaVersions.length > 0 ? versionData.cudaVersions[0] : null);
  };

  const updateResource = (field: 'gpu_type' | 'gpu_vram' | 'cpu_ram' | 'cpu_cores' | 'disk', value: string | number | undefined) => {
    setSelectedResources((prev) => ({
      ...prev,
      [field]: value,
    }));
  };

  const matchingNodes = useMemo(() => {
    return [...allNodes].filter((node) => {
      if (node.status !== 'online') return false;
      if (selectedResources.gpu_type && node.gpu_type !== selectedResources.gpu_type) return false;
      if (selectedResources.gpu_vram != null && node.total_vram < selectedResources.gpu_vram) return false;
      if (selectedResources.cpu_ram != null && node.total_ram < selectedResources.cpu_ram) return false;
      if (selectedResources.cpu_cores != null && (node.cpu_cores ?? 0) < selectedResources.cpu_cores) return false;
      if (selectedResources.disk != null && (node.available_disk ?? 0) < selectedResources.disk) return false;
      return true;
    }).sort((a, b) => {
      if (a.running_jobs !== b.running_jobs) return a.running_jobs - b.running_jobs;
      return (b.available_vram ?? 0) - (a.available_vram ?? 0);
    });
  }, [allNodes, selectedResources]);

  const refreshNodes = () => {
    fetchAllNodes()
      .then((nodes) => {
        setAllNodes(nodes);
        setNodesError(null);
      })
      .catch((e) => setNodesError(e instanceof Error ? e.message : 'Failed to load machines.'));
  };

  useEffect(() => {
    const fetchSummary = async () => {
      if (!resourceOptions || allNodes.length === 0) return;
      setLoadingSummary(true);
      try {
        const data = await fetchResourceSummary({
          gpu_type: selectedResources.gpu_type,
          gpu_vram: selectedResources.gpu_vram,
          cpu_ram: selectedResources.cpu_ram,
          cpu_cores: selectedResources.cpu_cores,
          disk: selectedResources.disk,
          op: 'ge',
        });
        setResourceSummary(data);
      } catch {
        setResourceSummary(null);
      } finally {
        setLoadingSummary(false);
      }
    };
    fetchSummary();
  }, [selectedResources, resourceOptions, allNodes]);

  const handleCreate = async () => {
    if (mode === 'existing' && !selectedJobId) return;
    let payload: Parameters<typeof submitInteractiveDirect>[0] | null = null;
    if (mode === 'direct') {
      if (!zipFile) {
        setSubmitError('Please upload a zip archive of your project.');
        return;
      }
      // Resolution precedence: custom base image > PyTorch (+CUDA variant)
      // image > plain Python image.
      const override = customBaseImage.trim();
      if (override) {
        payload = { name: sessionName, pythonVersion: '', baseImage: override };
      } else if (selectedPyTorch && selectedCuda) {
        payload = {
          name: sessionName,
          pythonVersion: '',
          pytorchVersion: selectedPyTorch,
          cudaVersion: selectedCuda.cuda,
          baseImage: selectedCuda.tag,
        };
      } else if (selectedPyTorch) {
        payload = { name: sessionName, pythonVersion: '', pytorchVersion: selectedPyTorch };
      } else {
        payload = { name: sessionName, pythonVersion };
      }
    }

    setSubmitting(true);
    setSubmitError(null);
    setSession(null);
    try {
      if (mode === 'direct' && payload) {
        const res = await submitInteractiveDirect(payload, zipFile!);
        setSession(res);
      } else {
        const res = await submitInteractiveSession(selectedJobId, sessionName);
        setSession(res);
      }
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : 'Failed to create interactive session.');
    } finally {
      setSubmitting(false);
    }
  };

  const resetForm = () => {
    setSelectedJobId('');
    setZipFile(null);
    setPythonVersion('3.11');
    setSelectedPyTorch('');
    setSelectedCuda(null);
    setCustomBaseImage('');
    setSessionName('');
    setSession(null);
    setSubmitError(null);
    setSelectedResources({ op: 'ge' });
    setResourceSummary(null);
  };

  const switchMode = (next: Mode) => {
    setMode(next);
    setSession(null);
    setSubmitError(null);
  };

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
        <Loader2 className="animate-spin text-blue-500" size={32} />
      </div>
    );
  }

  const showEmptyState = mode === 'existing' && jobs.length === 0 && !loadError;

  return (
    <div className="fade-in">
      <h1>Interactive</h1>

      <div className="card demo-banner">
        <Terminal size={18} color="var(--status-pending)" />
        <p style={{ margin: 0 }}>
          Create an interactive SSH + Tailscale sandbox session in two ways: derive it from a job
          you already built, or upload a project directly and pick your environment (Python,
          optionally PyTorch and CUDA). No training code is run — you get a shell in the container.
          You can track the build progress in your dashboard.
        </p>
      </div>

      {loadError && (
        <div className="card demo-banner" style={{ borderColor: 'rgba(239, 68, 68, 0.4)' }}>
          <p className="error-text" style={{ margin: 0 }}>Failed to load data: {loadError}</p>
        </div>
      )}

      <div style={{ display: 'flex', gap: '0.5rem', margin: '0 0 1rem' }}>
        <button
          className={`btn ${mode === 'existing' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => switchMode('existing')}
          style={{ padding: '0.45rem 1rem' }}
        >
          <Box size={15} /> From Existing Job
        </button>
        <button
          className={`btn ${mode === 'direct' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => switchMode('direct')}
          style={{ padding: '0.45rem 1rem' }}
        >
          <Upload size={15} /> Direct Upload
        </button>
      </div>

      {showEmptyState ? (
        <div className="card" style={{ textAlign: 'center', padding: '3rem 1.5rem' }}>
          <Box size={32} color="var(--text-secondary)" style={{ marginBottom: '0.75rem' }} />
          <h3 style={{ marginBottom: '0.5rem' }}>No Jobs Found</h3>
          <p style={{ margin: 0 }}>
            You need at least one job before creating an interactive session from an existing job —
            or switch to Direct Upload above to start one straight from your code.
          </p>
        </div>
      ) : (
        <div className="card" style={{ marginBottom: '1.5rem' }}>
           <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1rem' }}>
             <Terminal size={18} color="var(--text-secondary)" />
             New Interactive Session
           </h3>

           {/* ─── System Resource Selection ─── */}
           <div className="form-group" style={{ marginTop: '1.5rem' }}>
             <label className="form-label">System Resource</label>

             <div
               style={{
                 display: 'flex',
                 alignItems: 'flex-start',
                 gap: '0.5rem',
                 marginTop: '0.75rem',
                 padding: '0.75rem',
                 backgroundColor: 'rgba(255, 176, 0, 0.06)',
                 border: '1px solid rgba(255, 176, 0, 0.35)',
                 borderRadius: '6px',
               }}
             >
               <AlertTriangle size={16} style={{ flexShrink: 0, marginTop: '0.15rem' }} color="var(--accent-amber)" />
               <span style={{ fontSize: '0.825rem', lineHeight: 1.5, color: 'var(--text-secondary)' }}>
                 <strong style={{ color: 'var(--accent-amber)' }}>Tip:</strong> Selecting lower-end resources
                 (fewer GPUs, less VRAM) will help you get a machine faster — more machines in
                 the cluster will satisfy your request.
               </span>
             </div>

             {loadingResources ? (
               <div style={{ marginTop: '1rem', display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text-secondary)' }}>
                 <Loader2 className="animate-spin" size={16} />
                 <span>Loading resource options…</span>
               </div>
             ) : resourceOptionsError ? (
               <p className="error-text" style={{ marginTop: '1rem' }}>{resourceOptionsError}</p>
             ) : !resourceOptions ? (
               <p className="error-text" style={{ marginTop: '1rem' }}>No resource options available.</p>
             ) : (
               <div className="filter-grid" style={{ marginTop: '1rem' }}>
                 <div>
                   <label className="form-label">GPU Type</label>
                   <select
                     className="form-select"
                     value={selectedResources.gpu_type ?? ''}
                     onChange={(e) => updateResource('gpu_type', e.target.value || undefined)}
                   >
                     <option value="">Any GPU type</option>
                     {resourceOptions.gpu_types.map((t) => (
                       <option key={t} value={t}>{t}</option>
                     ))}
                   </select>
                 </div>
                 <div>
                   <label className="form-label">Min VRAM (GB)</label>
                   <select
                     className="form-select"
                     value={selectedResources.gpu_vram ?? ''}
                     onChange={(e) => updateResource('gpu_vram', e.target.value ? Number(e.target.value) : undefined)}
                   >
                     <option value="">Any VRAM</option>
                     {resourceOptions.vram_options.map((v) => (
                       <option key={v} value={v}>{v} GB</option>
                     ))}
                   </select>
                 </div>
                 <div>
                   <label className="form-label">Min RAM (GB)</label>
                   <select
                     className="form-select"
                     value={selectedResources.cpu_ram ?? ''}
                     onChange={(e) => updateResource('cpu_ram', e.target.value ? Number(e.target.value) : undefined)}
                   >
                     <option value="">Any RAM</option>
                     {resourceOptions.ram_options.map((v) => (
                       <option key={v} value={v}>{v} GB</option>
                     ))}
                   </select>
                 </div>
                 <div>
                   <label className="form-label">Min CPU Cores</label>
                   <select
                     className="form-select"
                     value={selectedResources.cpu_cores ?? ''}
                     onChange={(e) => updateResource('cpu_cores', e.target.value ? Number(e.target.value) : undefined)}
                   >
                     <option value="">Any cores</option>
                     {resourceOptions.core_options.map((v) => (
                       <option key={v} value={v}>{v}</option>
                     ))}
                   </select>
                 </div>
                 <div>
                   <label className="form-label">Min Disk (GB)</label>
                   <select
                     className="form-select"
                     value={selectedResources.disk ?? ''}
                     onChange={(e) => updateResource('disk', e.target.value ? Number(e.target.value) : undefined)}
                   >
                     <option value="">Any disk</option>
                     {resourceOptions.disk_options.map((v) => (
                       <option key={v} value={v}>{v} GB</option>
                     ))}
                   </select>
                 </div>
               </div>
             )}

              {allNodes.length === 0 && (
                <div style={{ marginTop: '1rem', display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                  <Loader2 className="animate-spin" size={16} />
                  <span style={{ fontSize: '0.825rem', color: 'var(--text-secondary)' }}>Loading machines…</span>
                </div>
              )}
              {allNodes.length === 0 && nodesError && (
                <div style={{ marginTop: '1rem', display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                  <p className="error-text" style={{ fontSize: '0.825rem', margin: 0 }}>
                    Failed to load machines: {nodesError}.
                  </p>
                  <button className="btn btn-secondary" onClick={refreshNodes} style={{ padding: '0.35rem 0.8rem', fontSize: '0.72rem' }}>
                    <RefreshCw size={12} /> Retry
                  </button>
                </div>
              )}

              {/* ─── Real-time Matching Machines ─── */}
              {allNodes.length > 0 && (
               <div style={{ marginTop: '1.5rem' }}>
                 <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.5rem', marginBottom: '0.75rem' }}>
                   <h4 style={{ margin: 0, fontSize: '0.9rem', color: 'var(--text-primary)' }}>
                     Matching Machines ({matchingNodes.length} {matchingNodes.length === 1 ? 'machine' : 'machines'})
                   </h4>
                   <button
                     className="btn btn-secondary"
                     onClick={refreshNodes}
                     style={{ padding: '0.35rem 0.8rem', fontSize: '0.75rem' }}
                   >
                     <RefreshCw size={12} /> Refresh
                   </button>
                 </div>

                 {!loadingSummary && resourceSummary && (
                   <div className="config-summary-grid">
                     <div className="config-summary-item">
                       <span className="config-summary-label">Matching Nodes</span>
                       <span className="config-summary-value">{resourceSummary.matching_nodes}</span>
                     </div>
                     <div className="config-summary-item">
                       <span className="config-summary-label">Avg Running Jobs</span>
                       <span className="config-summary-value">{resourceSummary.avg_running_jobs}</span>
                     </div>
                     <div className="config-summary-item">
                       <span className="config-summary-label">Queue Total</span>
                       <span className="config-summary-value">{resourceSummary.queue_total}</span>
                     </div>
                     <div className="config-summary-item">
                       <span className="config-summary-label">Queue Open</span>
                       <span className="config-summary-value">{resourceSummary.queue_open}</span>
                     </div>
                   </div>
                 )}

                 {matchingNodes.length === 0 ? (
                   <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginTop: '0.75rem' }}>
                     {nodesError
                       ? `Failed to load machines: ${nodesError}`
                       : 'No online machines match the selected resources.'}
                   </p>
                 ) : (
                   <div className="table-container" style={{ marginTop: '0.75rem' }}>
                     <table>
                       <thead>
                         <tr>
                           <th>Machine</th>
                           <th>GPU</th>
                           <th>VRAM</th>
                           <th>Running&nbsp;Jobs</th>
                           <th>Load (GPU&nbsp;/&nbsp;CPU&nbsp;/&nbsp;MEM)</th>
                         </tr>
                       </thead>
                       <tbody>
                         {matchingNodes.map((node) => (
                           <tr key={node.worker_id}>
                             <td style={{ fontWeight: 500 }}>
                               {node.hostname}
                               <span style={{ fontSize: '0.72rem', color: 'var(--text-secondary)', display: 'block' }}>
                                 {node.gpu_type} · {node.ip_address}
                               </span>
                             </td>
                             <td style={{ fontFamily: 'JetBrains Mono, monospace' }}>
                               {node.num_gpus} × {node.gpu_type}
                             </td>
                             <td>
                               {node.gpus_in_use}/{node.num_gpus} in use · {node.total_vram.toFixed(1)} GB total
                             </td>
                             <td>
                               <strong style={{ color: node.running_jobs > 0 ? 'var(--accent-amber)' : 'var(--status-success)' }}>
                                 {node.running_jobs}
                               </strong>
                             </td>
                             <td style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                               {node.gpu_load.toFixed(1)}% / {node.cpu_load.toFixed(1)}% / {node.mem_usage.toFixed(1)}%
                             </td>
                           </tr>
                         ))}
                       </tbody>
                     </table>
                   </div>
                 )}

                 {nodesError && matchingNodes.length > 0 && (
                   <p className="error-text" style={{ marginTop: '0.5rem', fontSize: '0.78rem' }}>
                     Note: {nodesError} — showing cached data. Click Refresh to retry.
                   </p>
                 )}
               </div>
             )}
           </div>

           {mode === 'existing' && (
            <>
              <div className="form-group">
                <label className="form-label">Base Job</label>
                <select
                  className="form-select"
                  value={selectedJobId}
                  onChange={(e) => {
                    setSelectedJobId(e.target.value);
                    setSession(null);
                    setSubmitError(null);
                  }}
                >
                  <option value="">Select a job...</option>
                  {jobs.map((job) => (
                    <option key={job.id} value={job.id}>
                      {job.name} · {job.status} · {job.id}
                    </option>
                  ))}
                </select>
                {selectedJob && (
                  <p style={{ marginTop: '0.5rem', fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                    A sandbox image will be built on top of this job's environment
                    {selectedJob.pytorchVersion !== 'unknown'
                      ? ` (PyTorch ${selectedJob.pytorchVersion}, CUDA ${selectedJob.cudaVersion})`
                      : ''}
                    .
                  </p>
                )}
              </div>
            </>
          )}

          {mode === 'direct' && (
            <>
              <div className="form-group">
                <label className="form-label">Project Archive (.zip)</label>
                <input
                  className="form-select"
                  type="file"
                  accept=".zip"
                  onChange={(e) => {
                    setZipFile(e.target.files?.[0] ?? null);
                    setSession(null);
                    setSubmitError(null);
                  }}
                />
                <p style={{ marginTop: '0.5rem', fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                  Your code is copied into /workspace inside the sandbox; requirements.txt is
                  installed automatically if present.
                </p>
              </div>

              <div className="form-group">
                <label className="form-label">Python Version</label>
                <select
                  className="form-select"
                  value={pythonVersion}
                  disabled={!!customBaseImage.trim()}
                  onChange={(e) => {
                    setPythonVersion(e.target.value);
                    setSession(null);
                    setSubmitError(null);
                  }}
                >
                  {PYTHON_VERSIONS.map((v) => (
                    <option key={v} value={v}>Python {v}</option>
                  ))}
                </select>
              </div>

              <div className="form-group">
                <label className="form-label">PyTorch Version (optional)</label>
                <select
                  className="form-select"
                  value={selectedPyTorch}
                  disabled={loadingVersions || !!customBaseImage.trim()}
                  onChange={(e) => handlePyTorchChange(e.target.value)}
                >
                  <option value="">No PyTorch (plain Python image)</option>
                  {loadingVersions && <option>Loading versions...</option>}
                  {!loadingVersions && versions.length === 0 && (
                    <option disabled>{versionError || 'No versions available'}</option>
                  )}
                  {versions.map((v) => (
                    <option key={v.version} value={v.version}>PyTorch {v.version}</option>
                  ))}
                </select>
                {versionError && selectedPyTorch === '' && (
                  <p style={{ fontSize: '0.75rem', color: 'var(--status-failed)', marginTop: '0.25rem' }}>
                    {versionError} — you can still use a plain Python image or a custom base image.
                  </p>
                )}
              </div>

              <div className="form-group">
                <label className="form-label">CUDA Version (optional, requires PyTorch)</label>
                <select
                  className="form-select"
                  value={selectedCuda?.tag ?? ''}
                  disabled={!selectedPyTorch || loadingVersions || availableCudas.length === 0 || !!customBaseImage.trim()}
                  onChange={(e) => {
                    setSelectedCuda(availableCudas.find((v) => v.tag === e.target.value) ?? null);
                    setSession(null);
                    setSubmitError(null);
                  }}
                >
                  {availableCudas.length === 0 ? (
                    <option value="">
                      {selectedPyTorch ? 'No CUDA variants for this version' : 'Select PyTorch first...'}
                    </option>
                  ) : (
                    availableCudas.map((c) => (
                      <option key={c.tag} value={c.tag}>CUDA {c.cuda} / cuDNN {c.cudnn}</option>
                    ))
                  )}
                </select>
                {selectedPyTorch && !customBaseImage.trim() && (
                  <p style={{ marginTop: '0.5rem', fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                    Base image: <code>{selectedCuda?.tag ?? `pytorch/pytorch:${selectedPyTorch}`}</code>
                  </p>
                )}
              </div>

              {!customBaseImage.trim() && !selectedPyTorch && (
                <p style={{ marginTop: '-0.5rem', marginBottom: '1rem', fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                  Base image: <code>python:{pythonVersion}-slim</code>
                </p>
              )}

              <div className="form-group">
                <label className="form-label">Custom Base Image (optional override)</label>
                <input
                  className="form-select"
                  type="text"
                  placeholder="e.g. nvcr.io/nvidia/pytorch:24.05-py3"
                  value={customBaseImage}
                  onChange={(e) => {
                    setCustomBaseImage(e.target.value);
                    setSession(null);
                    setSubmitError(null);
                  }}
                />
                <p style={{ marginTop: '0.5rem', fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                  If set, this fully overrides the selections above for maximum flexibility.
                </p>
              </div>
            </>
          )}

          <div className="form-group">
            <label className="form-label">Session Name (optional)</label>
            <input
              className="form-select"
              type="text"
              placeholder="e.g. debug-session-1"
              value={sessionName}
              onChange={(e) => setSessionName(e.target.value)}
            />
          </div>

          {(session || submitError) && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginTop: '0.75rem' }}>
              {(session || submitError) && (
                <button
                  className="btn btn-secondary"
                  onClick={resetForm}
                  style={{ padding: '0.4rem 0.9rem' }}
                >
                  <RotateCcw size={14} /> Clear
                </button>
              )}
            </div>
          )}

          {session && (
            <div className="acquire-success" style={{ marginTop: '1rem' }}>
              <CheckCircle2 size={20} color="var(--status-success)" />
              <div>
                {session.status === 'INTERACTIVE_READY' ? (
                  <p style={{ margin: 0 }}>
                    An interactive session already exists — reusing it, no rebuild needed.
                  </p>
                ) : (
                  <p style={{ margin: 0 }}>
                    Interactive session created! Build is running in the background.
                  </p>
                )}
                <p style={{ margin: '0.5rem 0 0', fontSize: '0.85rem', fontFamily: 'monospace' }}>
                  Session ID: {session.id}
                  <br />
                  Image tag: {'<docker-hub-user>'}/{session.id}-interactive:latest
                </p>
              </div>
            </div>
          )}
          {submitError && <p className="error-text" style={{ marginTop: '1rem' }}>{submitError}</p>}

          <div className="config-acquire-bar">
            <div className="config-acquire-info">
              {mode === 'direct' ? (
                zipFile ? (
                  <>
                    Building a standalone sandbox from{' '}
                    <strong>{zipFile.name}</strong>
                    {customBaseImage.trim()
                      ? <> on <strong>{customBaseImage.trim()}</strong></>
                      : selectedPyTorch
                        ? <> on <strong>{selectedCuda?.tag ?? `pytorch/pytorch:${selectedPyTorch}`}</strong> (Python {pythonVersion} default)</>
                        : <> with Python <strong>{pythonVersion}</strong></>}
                  </>
                ) : (
                  <>Upload a zip archive and pick your environment to enable session creation.</>
                )
            ) : selectedJob ? (
                <>
                  Creating a session from <strong>{selectedJob.name}</strong>
                </>
              ) : (
                <>Select one of your jobs above to enable session creation.</>
              )}
            </div>
            <button
              className="btn btn-primary"
              onClick={handleCreate}
              disabled={submitting || (mode === 'existing' ? !selectedJobId : !zipFile)}
            >
              {submitting ? <Loader2 className="animate-spin" size={18} /> : <Terminal size={18} />}
              {submitting ? 'Creating...' : 'Create Interactive Session'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

export default Interactive;
