import { api, getToken, type ApiError } from './api';

export type JobStatus = 'Pending' | 'Building' | 'Running' | 'Completed' | 'Failed';

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
}

interface BackendJob {
  id: string;
  user_id: string;
  object_key: string;
  command: string;
  docker_base_image: string;
  config: unknown;
  status: string;
  priority: string;
  reason_for_priority: string | null;
  vram_required: number | null;
  step_time: number | null;
  gpu_hour: number | null;
  created_at: string;
  updated_at: string;
}

const DUMMY_DEVICES = ['A100 80GB', 'H100 80GB', 'L4 24GB', 'V100 16GB'];

const mapStatus = (status: string): JobStatus => {
  switch (status) {
    case 'NOT_RUNNABLE': return 'Pending';
    case 'VRAM_ESTIMATION_PENDING': return 'Building';
    case 'RUNNABLE': return 'Pending';
    case 'IN_PROGRESS': return 'Running';
    case 'COMPLETED': return 'Completed';
    case 'FAILED': return 'Failed';
    default: return 'Pending';
  }
};

const parseEnvironment = (image: string): { pytorchVersion: string; cudaVersion: string } => {
  const tag = image.split(':').pop() ?? '';
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
  const firstLine = job.command.split('\n').map(line => line.trim()).find(line => line.length > 0);
  return firstLine ?? job.id;
};

const mapJob = (job: BackendJob, index: number): Job => {
  const env = parseEnvironment(job.docker_base_image);
  return {
    id: job.id,
    name: getJobName(job),
    status: mapStatus(job.status),
    pytorchVersion: env.pytorchVersion,
    cudaVersion: env.cudaVersion,
    submittedAt: job.created_at,
    gpuHours: job.gpu_hour ?? 0,
    device: parseDevice(job),
  };
};

let mockJobs: Job[] = [
  {
    id: 'job-101',
    name: 'ResNet50_ImageNet',
    status: 'Running',
    pytorchVersion: '2.3.1',
    cudaVersion: '12.1',
    submittedAt: new Date(Date.now() - 3600000).toISOString(),
    gpuHours: 1.2,
    device: 'A100 80GB',
    queuePosition: 0
  },
  {
    id: 'job-100',
    name: 'LLama3_Finetune',
    status: 'Completed',
    pytorchVersion: '2.1.2',
    cudaVersion: '11.8',
    submittedAt: new Date(Date.now() - 86400000).toISOString(),
    gpuHours: 14.5,
    device: 'H100 80GB',
    queuePosition: 0
  },
  {
    id: 'job-102',
    name: 'Bert_Classification',
    status: 'Failed',
    pytorchVersion: '2.2.0',
    cudaVersion: '11.8',
    submittedAt: new Date(Date.now() - 7200000).toISOString(),
    gpuHours: 0.1,
    device: 'L4 24GB',
    queuePosition: 0
  }
];

const hasError = (body: unknown): body is { error: string } => {
  return body !== null && typeof body === 'object' && 'error' in body;
};

export const fetchJobs = async (): Promise<Job[]> => {
  try {
    const data = await api.get<{ jobs?: BackendJob[] } | { error: string }>('/jobs/mine');
    if (hasError(data)) {
      throw new Error(data.error);
    }
    return (data.jobs ?? []).map(mapJob);
  } catch {
    return [...mockJobs];
  }
};

export const fetchJobById = async (id: string): Promise<Job | undefined> => {
  try {
    const data = await api.get<BackendJob | { error: string }>(`/jobs/${id}`);
    if (hasError(data)) {
      throw new Error(data.error);
    }
    return mapJob(data as BackendJob, 0);
  } catch {
    return mockJobs.find(j => j.id === id);
  }
};

export interface SubmitJobPayload {
  name: string;
  command: string;
  pytorchVersion: string;
  cudaVersion: string;
  dockerBaseImage: string;
  requestForPriority: boolean;
  reasonForPriority?: string;
}

const API_BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

export const submitJob = async (
  jobData: SubmitJobPayload,
  zipFile: File,
): Promise<Job> => {
  const formData = new FormData();
  formData.append('zip_file', zipFile);
  formData.append('command', jobData.command);
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
    device: DUMMY_DEVICES[0],
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
