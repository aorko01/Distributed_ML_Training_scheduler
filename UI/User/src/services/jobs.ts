import { getToken, type ApiError } from './api';

export type JobStatus = 'Pending' | 'Building' | 'Running' | 'Completed' | 'Failed';

export interface Job {
  id: string;
  name: string;
  status: JobStatus;
  pytorchVersion: string;
  cudaVersion: string;
  submittedAt: string;
  gpuHours: number;
  queuePosition?: number;
}

let mockJobs: Job[] = [
  {
    id: 'job-101',
    name: 'ResNet50_ImageNet',
    status: 'Running',
    pytorchVersion: '2.3.1',
    cudaVersion: '12.1',
    submittedAt: new Date(Date.now() - 3600000).toISOString(),
    gpuHours: 1.2,
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
    queuePosition: 0
  }
];

export const fetchJobs = async (): Promise<Job[]> => {
  return new Promise((resolve) => {
    setTimeout(() => resolve([...mockJobs]), 500);
  });
};

export const fetchJobById = async (id: string): Promise<Job | undefined> => {
  return new Promise((resolve) => {
    setTimeout(() => resolve(mockJobs.find(j => j.id === id)), 300);
  });
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
    queuePosition: undefined,
  };
};

type LogCallback = (log: { type: 'info' | 'warn' | 'error' | 'success', text: string, timestamp: string }) => void;

export const streamJobLogs = (_id: string, callback: LogCallback): () => void => {
  let count = 0;
  const baseLogs: Array<{ type: 'info' | 'warn' | 'error' | 'success', text: string }> = [
    { type: 'info', text: 'Initializing Docker build environment...' },
    { type: 'info', text: `Pulling base image pytorch/pytorch...` },
    { type: 'info', text: 'Extracting user workspace zip...' },
    { type: 'success', text: 'Workspace mounted successfully.' },
    { type: 'info', text: 'Running bash script...' },
    { type: 'warn', text: 'Warning: Unused import detected in script.' },
  ];

  for (let i = 1; i <= 50; i++) {
    baseLogs.push({ type: 'info', text: `Epoch ${i}/100: Loss ${(2.5 / Math.sqrt(i)).toFixed(4)}, Accuracy ${(40 + i).toFixed(2)}%` });
  }
  
  baseLogs.push({ type: 'error', text: 'Error: CUDA out of memory. Tried to allocate 128.00 MiB...' });

  const interval = setInterval(() => {
    if (count < baseLogs.length) {
      const log = baseLogs[count];
      callback({
        ...log,
        timestamp: new Date().toISOString()
      });
      count++;
    } else {
      clearInterval(interval);
    }
  }, 300);

  return () => clearInterval(interval);
};
