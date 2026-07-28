import React, { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import {
  Activity,
  ArrowRight,
  BarChart3,
  CheckCircle,
  Clock,
  Cpu,
  Download,
  FileText,
  Gauge,
  History,
  Layers,
  Loader2,
  Play,
  ShieldCheck,
  Sparkles,
  Upload,
  XCircle,
} from 'lucide-react';
import { ActiveJob, DashboardData, getDashboardData, getJobLogs, isApiMode, submitJob } from '../lib/mockApi';

const PYTORCH_CUDA_COMPAT: Record<string, string[]> = {
  '2.3': ['CUDA 11.8', 'CUDA 12.1'],
  '2.2': ['CUDA 11.8', 'CUDA 12.1'],
  '2.1': ['CUDA 11.8', 'CUDA 12.1'],
  '2.0': ['CUDA 11.7', 'CUDA 11.8'],
  '1.13': ['CUDA 11.6', 'CUDA 11.7'],
};

type DashboardTab = 'new-submission' | 'active' | 'history';

const tabs: Array<{ id: DashboardTab; label: string; icon: typeof FileText }> = [
  { id: 'new-submission', label: 'New Submission', icon: FileText },
  { id: 'active', label: 'Active Experiments', icon: Activity },
  { id: 'history', label: 'Job History', icon: History },
];

function statusClass(status: ActiveJob['status'] | 'Success' | 'Failed') {
  if (status === 'Success') return 'bg-green-900/30 text-green-400 border border-green-800';
  if (status === 'Failed') return 'bg-red-900/30 text-red-400 border border-red-800';
  if (status === 'Running') return 'bg-blue-900/30 text-blue-400 border border-blue-800 animate-pulse';
  if (status === 'Provisioning') return 'bg-indigo-900/30 text-indigo-400 border border-indigo-800';
  return 'bg-yellow-900/30 text-yellow-400 border border-yellow-800';
}

function statusIcon(status: ActiveJob['status'] | 'Success' | 'Failed') {
  if (status === 'Success') return <CheckCircle size={12} />;
  if (status === 'Failed') return <XCircle size={12} />;
  if (status === 'Running') return <Activity size={12} />;
  return <Clock size={12} />;
}

function feedToneClass(tone: ActivityItem['tone']) {
  if (tone === 'success') return 'border-green-800/70 bg-green-950/40 text-green-300';
  if (tone === 'warning') return 'border-amber-800/70 bg-amber-950/40 text-amber-300';
  return 'border-sky-800/70 bg-sky-950/40 text-sky-300';
}

function progressTone(progress: number) {
  if (progress >= 80) return 'from-emerald-400 to-green-500';
  if (progress >= 40) return 'from-cyan-400 to-sky-500';
  return 'from-amber-400 to-yellow-500';
}

export const ResearcherDashboard = () => {
  const [activeTab, setActiveTab] = useState<DashboardTab>('active');
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [showSuccessToast, setShowSuccessToast] = useState(false);

  const [isDragging, setIsDragging] = useState(false);
  const [uploadedFile, setUploadedFile] = useState<File | null>(null);
  const [projectTitle, setProjectTitle] = useState('Transformer Attention Analysis');
  const [projectDescription, setProjectDescription] = useState(
    'Benchmarking mixed precision attention kernels on large transformer workloads.',
  );
  const [runCommand, setRunCommand] = useState('python train.py --config configs/default.yaml');
  const [vramMode, setVramMode] = useState<'auto' | 'manual'>('auto');
  const [vram, setVram] = useState(24);
  const [torchVersion, setTorchVersion] = useState('2.3');
  const [cudaVersion, setCudaVersion] = useState('CUDA 12.1');

  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // Log viewer modal — null means closed; when set, displays fetched/mock log content.
  const [selectedLog, setSelectedLog] = useState<{ id: string; content: string } | null>(null);

  const loadDashboard = async () => {
    try {
      setLoadError(null);
      const data = await getDashboardData();
      setDashboard(data);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : 'Failed to load dashboard data.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void loadDashboard();
  }, []);

  useEffect(() => {
    if (torchVersion && !PYTORCH_CUDA_COMPAT[torchVersion]?.includes(cudaVersion)) {
      setCudaVersion(PYTORCH_CUDA_COMPAT[torchVersion]?.[0] ?? '');
    }
  }, [torchVersion, cudaVersion]);

  const handleDragOver = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = () => setIsDragging(false);

  const handleDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setIsDragging(false);
    setUploadedFile(event.dataTransfer.files.item(0));
  };

  const handleSubmitJob = async () => {
    if (!projectTitle.trim() || !cudaVersion || !torchVersion || !uploadedFile || !dashboard) {
      return;
    }

    setIsSubmitting(true);

    try {
      const nextDashboard = await submitJob({
        projectTitle: projectTitle.trim(),
        description: projectDescription.trim(),
        runCommand: runCommand.trim(),
        vramMode,
        vram,
        torchVersion,
        cudaVersion,
        assetFile: uploadedFile,
      });

      setDashboard(nextDashboard);
      setProjectTitle('');
      setProjectDescription('');
      setRunCommand('python train.py --config configs/default.yaml');
      setVramMode('auto');
      setVram(24);
      setTorchVersion('2.3');
      setCudaVersion('CUDA 12.1');
      setUploadedFile(null);
      setShowSuccessToast(true);

      window.setTimeout(() => setShowSuccessToast(false), 3000);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : 'Failed to submit job.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const renderActiveJobs = () => {
    const jobs = dashboard?.activeJobs ?? [];

    return (
      <div className="bg-[#1e1e1e]/75 backdrop-blur-sm rounded-2xl border border-[#333] shadow-xl overflow-hidden">
        <div className="p-6 border-b border-[#333] flex items-center justify-between gap-4">
          <div>
            <h2 className="text-xl font-bold text-white flex items-center gap-2">
              <Activity className="text-[#00ff41]" size={20} /> Active Experiments
            </h2>
            <p className="text-sm text-gray-500 mt-1">Live queue state from the scheduler.</p>
          </div>
          <button
            onClick={() => void loadDashboard()}
            className="text-sm text-[#00ff41] hover:text-[#00cc33] transition-colors inline-flex items-center gap-1"
          >
            Refresh <ArrowRight size={14} />
          </button>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left min-w-[920px]">
            <thead>
              <tr className="bg-[#121212] text-gray-400 text-sm">
                <th className="p-4 font-medium">Job ID</th>
                <th className="p-4 font-medium">Project</th>
                <th className="p-4 font-medium">GPU</th>
                <th className="p-4 font-medium">Status</th>
                <th className="p-4 font-medium">Progress</th>
                <th className="p-4 font-medium">Submitted</th>
                <th className="p-4 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#333]">
              {jobs.map((job) => (
                <tr key={job.id} className="hover:bg-[#252525] transition-colors">
                  <td className="p-4 text-gray-300 font-mono text-sm">{job.id}</td>
                  <td className="p-4">
                    <div className="text-white font-medium">{job.project}</div>
                    <div className="text-xs text-gray-500 mt-1">{job.description}</div>
                  </td>
                  <td className="p-4 text-gray-300 text-sm">
                    <div>{job.gpu}</div>
                    <div className="text-xs text-gray-500 mt-1">{job.type}</div>
                  </td>
                  <td className="p-4">
                    <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium ${statusClass(job.status)}`}>
                      {statusIcon(job.status)}
                      {job.status}
                      {job.status === 'Queued' && job.queuePos ? ` (#${job.queuePos})` : ''}
                    </span>
                  </td>
                  <td className="p-4 w-56">
                    <div className="flex items-center gap-3">
                      <div className="flex-1 h-2 rounded-full bg-[#101010] border border-[#333] overflow-hidden">
                        <div
                          className={`h-full bg-gradient-to-r ${progressTone(job.progress)} transition-all`}
                          style={{ width: `${job.progress}%` }}
                        />
                      </div>
                      <span className="text-xs text-gray-400 min-w-[3rem]">{job.progress}%</span>
                    </div>
                  </td>
                  <td className="p-4 text-gray-400 text-sm">{job.created}</td>
                  <td className="p-4 text-right space-x-2">
                    {/* Inspect Job — fetches logs and opens the inline modal */}
                    <button
                      className="text-[#00ff41] hover:text-[#00cc33] transition-colors"
                      title="Inspect Job"
                      onClick={() => {
                        void getJobLogs(job.id).then((content) =>
                          setSelectedLog({ id: job.id, content }),
                        );
                      }}
                    >
                      <BarChart3 size={16} />
                    </button>
                    {/* Download Logs — fetches logs and triggers a .txt file download */}
                    <button
                      className="text-gray-500 hover:text-white transition-colors"
                      title="Download Logs"
                      onClick={() => {
                        void getJobLogs(job.id).then((content) => {
                          const blob = new Blob([content], { type: 'text/plain' });
                          const url = URL.createObjectURL(blob);
                          const anchor = document.createElement('a');
                          anchor.href = url;
                          anchor.download = `${job.id}-logs.txt`;
                          anchor.click();
                          URL.revokeObjectURL(url);
                        });
                      }}
                    >
                      <Download size={16} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {jobs.length === 0 && <div className="p-8 text-center text-gray-500">No active jobs. Submit a new one to begin.</div>}

        {/* ── Log Viewer Modal ── rendered inside the card, above the empty-state */}
        <AnimatePresence>
          {selectedLog && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4"
              onClick={() => setSelectedLog(null)}
            >
              <motion.div
                initial={{ scale: 0.92, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                exit={{ scale: 0.92, opacity: 0 }}
                transition={{ type: 'spring', stiffness: 300, damping: 25 }}
                className="w-full max-w-2xl bg-[#121212] border border-[#00ff41]/30 rounded-2xl shadow-[0_0_40px_rgba(0,255,65,0.15)] overflow-hidden"
                onClick={(e) => e.stopPropagation()}
              >
                {/* modal header */}
                <div className="flex items-center justify-between px-6 py-4 border-b border-[#333]">
                  <div className="flex items-center gap-2">
                    <BarChart3 className="text-[#00ff41]" size={18} />
                    <span className="font-bold text-white text-sm">Job Logs</span>
                    <span className="font-mono text-xs text-gray-500 ml-2">{selectedLog.id}</span>
                  </div>
                  <button
                    onClick={() => setSelectedLog(null)}
                    className="text-gray-500 hover:text-white transition-colors"
                    title="Close"
                  >
                    <XCircle size={18} />
                  </button>
                </div>
                {/* log body */}
                <pre className="p-6 text-xs text-[#00ff41] font-mono leading-relaxed overflow-auto max-h-[60vh] whitespace-pre-wrap break-all">
                  {selectedLog.content}
                </pre>
                {/* modal footer */}
                <div className="px-6 py-3 border-t border-[#333] flex justify-end gap-3">
                  <button
                    onClick={() => {
                      const blob = new Blob([selectedLog.content], { type: 'text/plain' });
                      const url = URL.createObjectURL(blob);
                      const anchor = document.createElement('a');
                      anchor.href = url;
                      anchor.download = `${selectedLog.id}-logs.txt`;
                      anchor.click();
                      URL.revokeObjectURL(url);
                    }}
                    className="inline-flex items-center gap-2 rounded-lg border border-[#333] bg-[#1e1e1e] px-4 py-2 text-sm text-gray-300 hover:text-white hover:border-[#00ff41]/40 transition-colors"
                  >
                    <Download size={14} /> Download
                  </button>
                  <button
                    onClick={() => setSelectedLog(null)}
                    className="rounded-lg bg-[#00ff41]/10 border border-[#00ff41]/30 px-4 py-2 text-sm text-[#00ff41] hover:bg-[#00ff41]/20 transition-colors"
                  >
                    Close
                  </button>
                </div>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    );
  };

  const renderHistory = () => {
    const jobs = dashboard?.completedJobs ?? [];

    return (
      <div className="bg-[#1e1e1e]/75 backdrop-blur-sm rounded-2xl border border-[#333] shadow-xl overflow-hidden">
        <div className="p-6 border-b border-[#333] flex items-center justify-between gap-4">
          <div>
            <h2 className="text-xl font-bold text-white flex items-center gap-2">
              <History className="text-[#00ff41]" size={20} /> Job History
            </h2>
            <p className="text-sm text-gray-500 mt-1">Completed jobs with outcome, artifact, and runtime details.</p>
          </div>
          <button
            className="text-sm text-[#00ff41] hover:underline"
            onClick={() => {
              const jobs = dashboard?.completedJobs;
              if (!jobs || jobs.length === 0) return;

              const header = 'Job ID,Project,Date,Duration,Status,Score,Artifact\n';
              const rows = jobs
                .map((job) =>
                  [job.id, job.project, job.date, job.duration, job.status, job.score, job.artifact]
                    .map((v) => `"${String(v).replace(/"/g, '""')}"`)
                    .join(','),
                )
                .join('\n');

              const blob = new Blob([header + rows], { type: 'text/csv;charset=utf-8;' });
              const url = URL.createObjectURL(blob);
              const anchor = document.createElement('a');
              anchor.href = url;
              anchor.download = 'job-history.csv';
              anchor.click();
              URL.revokeObjectURL(url);
            }}
          >
            Export CSV
          </button>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left min-w-[920px]">
            <thead>
              <tr className="bg-[#121212] text-gray-400 text-sm">
                <th className="p-4 font-medium">Job ID</th>
                <th className="p-4 font-medium">Project</th>
                <th className="p-4 font-medium">Date</th>
                <th className="p-4 font-medium">Duration</th>
                <th className="p-4 font-medium">Status</th>
                <th className="p-4 font-medium">Score</th>
                <th className="p-4 font-medium text-right">Artifact</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#333]">
              {jobs.map((job) => (
                <tr key={job.id} className="hover:bg-[#252525] transition-colors">
                  <td className="p-4 text-gray-300 font-mono text-sm">{job.id}</td>
                  <td className="p-4 text-white font-medium">{job.project}</td>
                  <td className="p-4 text-gray-400 text-sm">{job.date}</td>
                  <td className="p-4 text-gray-400 text-sm">{job.duration}</td>
                  <td className="p-4">
                    <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium ${statusClass(job.status)}`}>
                      {statusIcon(job.status)}
                      {job.status}
                    </span>
                  </td>
                  <td className="p-4 text-gray-300 text-sm">{job.score}</td>
                  <td className="p-4 text-right text-sm text-[#00ff41]">{job.artifact}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  };

  const renderSubmission = () => (
    <div className="bg-[#1e1e1e]/75 backdrop-blur-sm p-6 md:p-8 rounded-2xl border border-[#333] shadow-xl">
      <div className="flex items-start justify-between gap-6 mb-6">
        <div>
          <h2 className="text-2xl font-bold text-white flex items-center gap-2">
            <Sparkles className="text-[#00ff41]" /> New Job Submission
          </h2>
          <p className="text-sm text-gray-500 mt-2">
            Upload a training archive to submit it to the scheduler.
          </p>
        </div>
        <div className="flex items-center gap-2 rounded-full border border-[#00ff41]/25 bg-[#00ff41]/10 px-3 py-1 text-xs text-[#00ff41]">
          <ShieldCheck size={14} /> API ready
        </div>
      </div>

      <div className="space-y-8">
        <div className="grid gap-6">
          <div>
            <label className="block text-sm font-medium text-gray-400 mb-1">Project Title</label>
            <input
              type="text"
              value={projectTitle}
              onChange={(event) => setProjectTitle(event.target.value)}
              placeholder="e.g., Transformer Attention Analysis"
              className="w-full bg-[#121212] border border-[#333] rounded-lg p-3 text-white focus:outline-none focus:border-[#00ff41] focus:ring-1 focus:ring-[#00ff41] transition-all"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-400 mb-1">Description</label>
            <textarea
              rows={3}
              value={projectDescription}
              onChange={(event) => setProjectDescription(event.target.value)}
              placeholder="Brief technical description..."
              className="w-full bg-[#121212] border border-[#333] rounded-lg p-3 text-white focus:outline-none focus:border-[#00ff41] focus:ring-1 focus:ring-[#00ff41] transition-all"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-400 mb-1">Run Command</label>
            <input
              type="text"
              value={runCommand}
              onChange={(event) => setRunCommand(event.target.value)}
              placeholder="e.g., python train.py --config configs/default.yaml"
              spellCheck={false}
              className="w-full bg-[#121212] border border-[#333] rounded-lg p-3 text-white font-mono text-sm focus:outline-none focus:border-[#00ff41] focus:ring-1 focus:ring-[#00ff41] transition-all"
            />
            <p className="text-xs text-gray-500 mt-1">Default command executed in the project workspace.</p>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div>
            <div className="flex items-center justify-between mb-3 gap-3">
              <label className="block text-sm font-medium text-gray-400">
                VRAM Requirement
                {vramMode === 'manual' && <span className="text-[#00ff41] ml-1">{vram} GB</span>}
              </label>
              <div className="flex bg-[#121212] border border-[#333] rounded-lg p-0.5">
                <button
                  type="button"
                  onClick={() => setVramMode('auto')}
                  className={`px-2.5 py-1 text-xs rounded-md transition-all ${vramMode === 'auto' ? 'bg-[#00ff41] text-black font-medium' : 'text-gray-400 hover:text-white'}`}
                >
                  Auto
                </button>
                <button
                  type="button"
                  onClick={() => setVramMode('manual')}
                  className={`px-2.5 py-1 text-xs rounded-md transition-all ${vramMode === 'manual' ? 'bg-[#00ff41] text-black font-medium' : 'text-gray-400 hover:text-white'}`}
                >
                  Manual
                </button>
              </div>
            </div>

            {vramMode === 'auto' ? (
              <div className="flex items-start gap-2 bg-[#121212] border border-[#333] rounded-lg p-3">
                <Cpu className="text-[#00ff41] shrink-0 mt-0.5" size={16} />
                <p className="text-xs text-gray-400">
                  VRAM will be estimated after submission by profiling the training bundle when the API is connected.
                </p>
              </div>
            ) : (
              <>
                <input
                  type="range"
                  min="8"
                  max="48"
                  step="4"
                  value={vram}
                  onChange={(event) => setVram(parseInt(event.target.value, 10))}
                  className="w-full h-2 bg-[#333] rounded-lg appearance-none cursor-pointer accent-[#00ff41]"
                />
                <div className="flex justify-between text-xs text-gray-500 mt-2">
                  <span>8GB</span>
                  <span>48GB</span>
                </div>
                <p className="text-xs text-gray-500 mt-2">Manually set the VRAM to reserve for this job.</p>
              </>
            )}
          </div>

          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-400 mb-1">PyTorch Version</label>
              <select
                value={torchVersion}
                onChange={(event) => setTorchVersion(event.target.value)}
                className="w-full bg-[#121212] border border-[#333] rounded-lg p-3 text-white focus:outline-none focus:border-[#00ff41]"
              >
                <option value="" disabled>
                  Select PyTorch version…
                </option>
                {Object.keys(PYTORCH_CUDA_COMPAT).map((version) => (
                  <option key={version} value={version}>
                    PyTorch {version}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-400 mb-1">CUDA Version</label>
              <select
                value={cudaVersion}
                onChange={(event) => setCudaVersion(event.target.value)}
                disabled={!torchVersion}
                className={`w-full bg-[#121212] border border-[#333] rounded-lg p-3 text-white focus:outline-none focus:border-[#00ff41] ${!torchVersion ? 'opacity-40 cursor-not-allowed' : ''}`}
              >
                <option value="" disabled>
                  {torchVersion ? 'Select CUDA version…' : 'Select PyTorch version first'}
                </option>
                {torchVersion && PYTORCH_CUDA_COMPAT[torchVersion].map((version) => (
                  <option key={version} value={version}>
                    {version}
                  </option>
                ))}
              </select>
              {torchVersion && <p className="text-xs text-gray-500 mt-1">Only compatible CUDA versions are shown.</p>}
            </div>
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-400 mb-2">Training Assets</label>
          <div
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            className={`border-2 border-dashed rounded-xl p-8 flex flex-col items-center justify-center transition-all cursor-pointer group ${isDragging ? 'border-[#00ff41] bg-[#00ff41]/10 scale-[1.02]' : 'border-[#00ff41]/40 bg-[#00ff41]/5 hover:bg-[#00ff41]/10'}`}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".zip"
              className="hidden"
              onChange={(event) => setUploadedFile(event.target.files?.[0] ?? null)}
            />

            {uploadedFile ? (
              <div className="flex flex-col items-center">
                <div className="w-12 h-12 rounded-full bg-[#00ff41]/20 flex items-center justify-center mb-4">
                  <CheckCircle className="text-[#00ff41]" size={24} />
                </div>
                <p className="text-[#00ff41] font-medium">{uploadedFile.name}</p>
                <p className="text-xs text-gray-500 mt-1">Ready for submission</p>
              </div>
            ) : (
              <>
                <div className="w-12 h-12 rounded-full bg-[#00ff41]/20 flex items-center justify-center mb-4 group-hover:scale-110 transition-transform">
                  <Upload className="text-[#00ff41]" size={24} />
                </div>
                <p className="text-gray-300 font-medium">Drop training script & datasets here</p>
                <p className="text-xs text-gray-500 mt-1">.zip only</p>
              </>
            )}
          </div>
          <div className="flex items-start gap-2 mt-3 bg-[#ff3b3b]/5 border border-[#ff3b3b]/30 rounded-lg p-3">
            <FileText className="text-[#ff3b3b] shrink-0 mt-0.5" size={16} />
            <p className="text-xs text-gray-400">
              <span className="text-[#ff3b3b] font-medium">Required:</span> archive root should include a{' '}
              <span className="font-mono text-gray-300">requirements.txt</span> so the environment can be built later.
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-end gap-3 pt-6 border-t border-[#333]">
          <button
            onClick={() => loadDashboard()}
            className="inline-flex items-center gap-2 rounded-lg border border-[#333] bg-[#121212] px-4 py-2 text-sm text-gray-300 hover:text-white hover:border-[#00ff41]/40 transition-colors"
          >
            <Gauge size={16} /> Reload scheduler data
          </button>
          <button
            onClick={handleSubmitJob}
            disabled={isSubmitting || !projectTitle.trim() || !torchVersion || !cudaVersion || !uploadedFile}
            className={`bg-[#00ff41] text-black font-bold py-2 px-6 rounded-lg transition-all shadow-[0_0_15px_rgba(0,255,65,0.4)] flex items-center gap-2 ${isSubmitting || !projectTitle.trim() || !torchVersion || !cudaVersion || !uploadedFile ? 'opacity-50 cursor-not-allowed' : 'hover:bg-[#00cc33]'}`}
          >
            {isSubmitting ? (
              <>
                <Loader2 size={16} className="animate-spin" /> Running checks...
              </>
            ) : (
              <>
                <Play size={16} /> Submit Job
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );

  if (isLoading) {
    return (
      <div className="min-h-screen bg-[#1a1a1a] text-gray-200 flex items-center justify-center">
        <div className="flex items-center gap-3 text-[#00ff41]">
          <Loader2 size={20} className="animate-spin" /> Loading dashboard...
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen bg-[#1a1a1a] text-gray-200 font-sans overflow-hidden">
      <motion.aside
        initial={{ x: -50, opacity: 0 }}
        animate={{ x: 0, opacity: 1 }}
        className="w-72 bg-[#121212]/85 backdrop-blur-md border-r border-[#333] flex flex-col z-20"
      >
        <div className="p-6 border-b border-[#333]">
          <h1 className="text-2xl font-bold text-[#00ff41] tracking-tighter flex items-center gap-2">
            <Cpu className="w-6 h-6" /> DistributeML
          </h1>
          <p className="text-xs text-gray-500 mt-1">Researcher Portal</p>
        </div>

        <nav className="flex-1 p-4 space-y-2">
          {tabs.map((item) => (
            <button
              key={item.id}
              onClick={() => setActiveTab(item.id)}
              className={`w-full flex items-center gap-3 px-4 py-3 rounded-lg transition-all duration-200 ${
                activeTab === item.id
                  ? 'bg-[#00ff41]/10 text-[#00ff41] shadow-[0_0_10px_rgba(0,255,65,0.1)] border border-[#00ff41]/20'
                  : 'text-gray-400 hover:bg-[#222] hover:text-white'
              }`}
            >
              <item.icon size={18} />
              <span className="font-medium">{item.label}</span>
            </button>
          ))}
        </nav>

        <div className="p-4 border-t border-[#333] text-xs text-gray-500 space-y-2">
          <div className="flex items-center gap-2 text-[#00ff41]">
            <ShieldCheck size={14} /> {isApiMode() ? 'Scheduler API connected' : 'Scheduler API unavailable'}
          </div>
          <div>Using localhost:8000 by default; set VITE_API_BASE_URL to override it.</div>
        </div>
      </motion.aside>

      <main className="flex-1 overflow-y-auto p-6 md:p-8 relative scrollbar-hide">
        <div className="absolute inset-0 pointer-events-none bg-[radial-gradient(ellipse_at_top_right,_var(--tw-gradient-stops))] from-[#00ff41]/5 via-transparent to-transparent" />

        <AnimatePresence>
          {showSuccessToast && (
            <motion.div
              initial={{ y: -50, opacity: 0 }}
              animate={{ y: 0, opacity: 1 }}
              exit={{ y: -50, opacity: 0 }}
              className="absolute top-4 right-4 z-50 bg-[#00ff41] text-black px-6 py-3 rounded-lg shadow-[0_0_20px_rgba(0,255,65,0.3)] flex items-center gap-3 font-bold"
            >
              <CheckCircle size={20} /> Job submitted successfully
            </motion.div>
          )}
        </AnimatePresence>

        <div className="relative z-10 max-w-[1200px] mx-auto space-y-6">
          {loadError && (
            <div className="rounded-2xl border border-red-800 bg-red-950/40 px-4 py-3 text-sm text-red-200">
              {loadError}
            </div>
          )}

          <div className="space-y-6">
            {activeTab === 'new-submission' && renderSubmission()}
            {activeTab === 'active' && renderActiveJobs()}
            {activeTab === 'history' && renderHistory()}
          </div>
        </div>
      </main>
    </div>
  );
};
