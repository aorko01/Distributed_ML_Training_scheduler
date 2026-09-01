import { api, getToken, type ApiError } from './api';

export type JobStatus =
  | 'Pending'
  | 'Building'
  | 'Running'
  | 'Completed'
  | 'Failed'
  | 'Retrying'
  | 'Interactive Ready'
  | 'Provisioning'
  | 'Interactive Running'
  | 'Stopped';

export interface Job {
  id: string;
  name: string;
  status: JobStatus;
  pytorchVersion: string;
  cudaVersion: string;
  submittedAt: string;
  gpuHours: number;
  device: string;
  queuePosition?: number;
  resumeCommand?: string;
}

interface BackendJob {
  id: string;
  user_id: string;
  object_key: string;
  name: string | null;
  command: string;
  resume_command: string | null;
  docker_base_image: string;
  config: unknown;
  status: string;
  priority: string;
  reason_for_priority: string | null;
  vram_required: number | null;
  step_time: number | null;
  gpu_hour: number | null;
  device: string | null;
  created_at: string;
  updated_at: string;
  build_type?: string;
  base_job_id?: string;
}

const mapStatus = (status: string): JobStatus => {
  // Normalize so backend casing (e.g. "interactive_ready" vs "INTERACTIVE_READY")
  // doesn't break the mapping.
  const normalized = (status ?? '').toUpperCase();
  switch (normalized) {
    case 'NOT_RUNNABLE': return 'Pending';
    case 'VRAM_ESTIMATION_PENDING': return 'Building';
    case 'RUNNABLE': return 'Provisioning';
    case 'IN_PROGRESS': return 'Running';
    case 'COMPLETED': return 'Completed';
    case 'FAILED': return 'Failed';
    case 'RETRY_NEEDED': return 'Retrying';
    case 'INTERACTIVE_READY': return 'Interactive Ready';
    case 'INTERACTIVE_DEPLOYING': return 'Provisioning';
    case 'INTERACTIVE_RUNNING': return 'Interactive Running';
    case 'INTERACTIVE_STOPPED': return 'Stopped';
    default: return 'Pending';
  }
};

const parseEnvironment = (image: string | null): { pytorchVersion: string; cudaVersion: string } => {
  const tag = (image ?? '').split(':').pop() ?? '';
  const match = tag.match(/^(?<pytorch>[\d.]+)-cuda(?<cuda>[\d.]+)-cudnn.*-runtime$/);
  return {
    pytorchVersion: match?.groups?.pytorch ?? 'unknown',
    cudaVersion: match?.groups?.cuda ?? 'unknown',
  };
};

const parseDevice = (job: BackendJob): string => {
  return job.device ?? 'N/A';
};

const getJobName = (job: BackendJob): string => {
  if (job.name) return job.name;
  const firstLine = job.command.split('\n').map(line => line.trim()).find(line => line.length > 0);
  return firstLine ?? job.id;
};

const isInteractiveJob = (job: BackendJob): boolean =>
  (job as { build_type?: string }).build_type === 'interactive';

const mapJob = (job: BackendJob): Job => {
  const env = parseEnvironment(job.docker_base_image);
  const interactive = isInteractiveJob(job);
  return {
    id: job.id,
    name: interactive && !job.name ? `Interactive session (${job.base_job_id ?? job.id})` : getJobName(job),
    status: mapStatus(job.status),
    pytorchVersion: env.pytorchVersion,
    cudaVersion: env.cudaVersion,
    submittedAt: job.created_at,
    gpuHours: job.gpu_hour ?? 0,
    device: parseDevice(job),
    resumeCommand: job.resume_command ?? undefined,
  };
};

const hasError = (body: unknown): body is { error: string } => {
  return body !== null && typeof body === 'object' && 'error' in body;
};

export const fetchJobs = async (builtOnly = false): Promise<Job[]> => {
  const data = await api.get<{ jobs?: BackendJob[] } | { error: string }>('/jobs/mine');
  if (hasError(data)) {
    throw new Error(data.error);
  }
  const jobs = data.jobs ?? [];
  // NOT_RUNNABLE means the image hasn't been built/pushed yet, so it can't
  // be used as a base for interactive sessions.
  return (builtOnly ? jobs.filter((j) => j.status !== 'NOT_RUNNABLE') : jobs).map(mapJob);
};

export const fetchJobById = async (id: string): Promise<Job | undefined> => {
  const data = await api.get<BackendJob | { error: string }>(`/jobs/${id}`);
  if (hasError(data)) {
    throw new Error(data.error);
  }
  return mapJob(data as BackendJob);
};

export interface JobConnectInfo {
  session_id: string;
  headscale_ip: string;
  gateway_host: string;
  gateway_ssh_port: number;
  ssh_user: string;
  container_user: string;
  ssh_password: string;
  ssh_password_ttl_seconds: number;
}

export const fetchJobConnectInfo = async (jobId: string): Promise<JobConnectInfo> => {
  const data = await api.get<JobConnectInfo | { error: string }>(`/jobs/${jobId}/connect`);
  if (hasError(data)) throw new Error(data.error);
  return data as JobConnectInfo;
};

export interface CommitInteractivePayload {
  command: string;
  resumeCommand?: string;
  priority: 'NORMAL' | 'REQUESTED' | 'HIGH';
  reasonForPriority?: string;
}

export interface CommitInteractiveResponse {
  job_id: string;
  session_id: string;
  image_tag: string;
  status: string;
}

export const commitInteractiveJob = async (
  jobId: string,
  payload: CommitInteractivePayload,
): Promise<CommitInteractiveResponse> => {
  const data = await api.post<CommitInteractiveResponse | { error: string }>(
    `/jobs/${jobId}/commit`,
    {
      command: payload.command,
      resume_command: payload.resumeCommand,
      priority: payload.priority,
      reason_for_priority: payload.reasonForPriority,
    },
  );
  if (hasError(data)) throw new Error((data as { error: string }).error);
  return data as CommitInteractiveResponse;
};

export interface SubmitJobPayload {
  name: string;
  command: string;
  resumeCommand?: string;
  pytorchVersion: string;
  cudaVersion: string;
  dockerBaseImage: string;
  requestForPriority: boolean;
  reasonForPriority?: string;
}

export interface InteractiveSession {
  id: string;
  baseJobId: string;
  status: string;
}

export const submitInteractiveSession = async (
  baseJobId: string,
  name?: string,
): Promise<InteractiveSession> => {
  const data = await api.post<
    | {
        id: string;
        status: string;
        base_job_id: string;
      }
    | { error: string }
  >('/jobs/submit_interactive', {
    base_job_id: baseJobId,
    ...(name && name.trim() ? { name: name.trim() } : {}),
  });

  if (hasError(data)) {
    throw new Error(data.error);
  }

  return {
    id: data.id,
    baseJobId: data.base_job_id,
    status: data.status,
  };
};

export interface SubmitInteractiveDirectPayload {
  name?: string;
  pythonVersion: string;
  pytorchVersion?: string;
  cudaVersion?: string;
  baseImage?: string;
}

export const submitInteractiveDirect = async (
  payload: SubmitInteractiveDirectPayload,
  zipFile: File,
): Promise<InteractiveSession> => {
  const formData = new FormData();
  formData.append('zip_file', zipFile);
  formData.append('python_version', payload.pythonVersion);
  if (payload.name && payload.name.trim()) {
    formData.append('name', payload.name.trim());
  }
  if (payload.pytorchVersion) {
    formData.append('pytorch_version', payload.pytorchVersion);
  }
  if (payload.cudaVersion) {
    formData.append('cuda_version', payload.cudaVersion);
  }
  if (payload.baseImage && payload.baseImage.trim()) {
    formData.append('base_image', payload.baseImage.trim());
  }

  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE_URL}/jobs/submit_interactive_direct`, {
    method: 'POST',
    headers,
    body: formData,
  });

  const contentType = response.headers.get('content-type') ?? '';
  const body = contentType.includes('application/json')
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const error = new Error(
      body && typeof body === 'object' && 'detail' in body
        ? String((body as Record<string, unknown>).detail)
        : `Request failed with status ${response.status}`,
    ) as ApiError;
    error.status = response.status;
    throw error;
  }

  const record = (body ?? {}) as Record<string, unknown>;
  if (typeof record.error === 'string') {
    throw new Error(record.error);
  }

  return {
    id: String(record.id ?? ''),
    baseJobId: '',
    status: String(record.status ?? ''),
  };
};

const API_BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

export const submitJob = async (
  jobData: SubmitJobPayload,
  zipFile: File,
): Promise<Job> => {
  const formData = new FormData();
  formData.append('zip_file', zipFile);
  formData.append('name', jobData.name);
  formData.append('command', jobData.command);
  if (jobData.resumeCommand) {
    formData.append('resume_command', jobData.resumeCommand);
  }
  formData.append('docker_base_image', jobData.dockerBaseImage);
  formData.append('request_for_priority', String(jobData.requestForPriority));
  if (jobData.reasonForPriority) {
    formData.append('reason_for_priority', jobData.reasonForPriority);
  }

  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE_URL}/jobs/submit_job`, {
    method: 'POST',
    headers,
    body: formData,
  });

  const contentType = response.headers.get('content-type') ?? '';
  const body = contentType.includes('application/json')
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const error = new Error(
      (body && typeof body === 'object' && 'detail' in body
        ? String((body as Record<string, unknown>).detail)
        : `Request failed with status ${response.status}`) ||
        `Request failed with status ${response.status}`,
    ) as ApiError;
    error.status = response.status;
    throw error;
  }

  const bodyRecord = (body ?? {}) as Record<string, unknown>;
  if (typeof bodyRecord.error === 'string') {
    throw new Error(bodyRecord.error);
  }

  const job = body as {
    id: string;
    status?: string;
    created_at?: string;
  };

  return {
    id: job.id,
    name: jobData.name,
    status: (job.status as JobStatus) ?? 'Building',
    pytorchVersion: jobData.pytorchVersion,
    cudaVersion: jobData.cudaVersion,
    submittedAt: job.created_at ?? new Date().toISOString(),
    gpuHours: 0,
    device: 'N/A',
    resumeCommand: jobData.resumeCommand,
    queuePosition: undefined,
  };
};

export interface LogLine {
  type: 'info' | 'warn' | 'error' | 'success';
  text: string;
  timestamp: string;
}

const classifyLogLine = (text: string): LogLine['type'] => {
  const lower = text.toLowerCase();
  if (/error|exception|traceback|failed/.test(lower)) return 'error';
  if (/warn/.test(lower)) return 'warn';
  if (/success|completed/.test(lower)) return 'success';
  return 'info';
};

export const downloadJobOutput = async (jobId: string, jobName: string): Promise<void> => {
  const token = getToken();
  const headers: Record<string, string> = {};
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE_URL}/jobs/${jobId}/download_output`, {
    method: 'GET',
    headers,
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(errorText || `Download failed with status ${response.status}`);
  }

  const blob = await response.blob();
  const downloadUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = downloadUrl;
  link.download = `${jobName.replace(/\s+/g, '_').toLowerCase()}-output.zip`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(downloadUrl);
};

export const fetchJobLogs = async (id: string): Promise<LogLine[]> => {
  try {
    const data = await api.get<{ content?: string } | { error: string }>(
      `/jobs/${id}/logs`,
    );
    if (hasError(data)) return [];
    const content = (data.content ?? '').replace(/\r\n/g, '\n').trim();
    if (!content) return [];
    const now = new Date().toISOString();
    return content.split('\n').filter(Boolean).map((text) => ({
      type: classifyLogLine(text),
      text,
      timestamp: now,
    }));
  } catch {
    return [];
  }
};

interface StreamLogEntry {
  id: string;
  line: string;
  ts: number;
}

export interface StreamJobLogsOptions {
  onLog: (line: LogLine) => void;
  onDone?: (status: string) => void;
}

const toLogLine = (entry: StreamLogEntry): LogLine => ({
  type: classifyLogLine(entry.line),
  text: entry.line,
  timestamp: new Date(entry.ts).toISOString(),
});

const mergeWithStored = (stored: LogLine[], history: LogLine[]): LogLine[] => {
  if (stored.length === 0) return history;

  const storedTexts = stored.map((line) => line.text);
  const historyTexts = history.map((line) => line.text);
  const maxK = Math.min(storedTexts.length, historyTexts.length);

  let overlap = 0;
  for (let k = maxK; k > 0; k -= 1) {
    if (
      historyTexts.slice(0, k).join('\n') ===
      storedTexts.slice(storedTexts.length - k).join('\n')
    ) {
      overlap = k;
      break;
    }
  }

  return history.slice(overlap);
};

export const streamJobLogs = (
  id: string,
  options: StreamJobLogsOptions,
): (() => void) => {
  const wsBaseUrl = API_BASE_URL.replace(/^http/, 'ws');
  const token = getToken() ?? '';

  let ws: WebSocket | null = null;
  let disposed = false;
  let finished = false;
  let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
  let lastStreamId: string | null = null;
  let storedLogs: LogLine[] = [];

  const connect = () => {
    if (disposed || finished) return;

    const params = new URLSearchParams({ token });
    if (lastStreamId) params.set('after', lastStreamId);

    ws = new WebSocket(`${wsBaseUrl}/jobs/${id}/logs/stream?${params.toString()}`);

    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data) as {
          type: string;
          lines?: StreamLogEntry[];
          id?: string;
          line?: string;
          ts?: number;
          status?: string;
        };

        if (message.type === 'init' && Array.isArray(message.lines)) {
          const history = message.lines.map(toLogLine);
          if (message.lines.length > 0) {
            lastStreamId = message.lines[message.lines.length - 1].id;
          }
          mergeWithStored(storedLogs, history).forEach((line) =>
            options.onLog(line),
          );
        } else if (message.type === 'log' && message.line != null) {
          if (message.id) lastStreamId = message.id;
          options.onLog(
            toLogLine({
              id: message.id ?? '',
              line: message.line,
              ts: message.ts ?? Date.now(),
            }),
          );
        } else if (message.type === 'done') {
          finished = true;
          options.onDone?.(message.status ?? '');
          ws?.close();
        }
      } catch {
        // Ignore malformed messages.
      }
    };

    ws.onclose = () => {
      if (!disposed && !finished) {
        reconnectTimer = setTimeout(connect, 2000);
      }
    };
    ws.onerror = () => ws?.close();
  };

  const start = async () => {
    // Show previous logs already persisted in the object store first,
    // then connect for realtime lines.
    storedLogs = await fetchJobLogs(id);
    if (disposed) return;
    storedLogs.forEach((line) => options.onLog(line));
    connect();
  };

  start();

  return () => {
    disposed = true;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    if (ws) ws.close();
  };
};
