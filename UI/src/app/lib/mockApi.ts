export type JobStatus = 'Running' | 'Queued' | 'Success' | 'Failed' | 'Provisioning';
export type ClusterStatus = 'Online' | 'Degraded' | 'Offline';

export interface ActiveJob {
  id: string;
  project: string;
  status: JobStatus;
  progress: number;
  queuePos?: number;
  type: string;
  created: string;
  gpu: string;
  owner: string;
  description: string;
  runCommand: string;
  estimatedDuration: string;
}

export interface CompletedJob {
  id: string;
  project: string;
  date: string;
  status: 'Success' | 'Failed';
  duration: string;
  gpu: string;
  score: string;
  artifact: string;
}

export interface ClusterNode {
  name: string;
  status: ClusterStatus;
  gpu: string;
  utilization: number;
  temperature: string;
}

export interface ActivityItem {
  id: string;
  label: string;
  detail: string;
  time: string;
  tone: 'success' | 'warning' | 'info';
}

export interface DashboardOverview {
  activeJobs: number;
  queuedJobs: number;
  runningJobs: number;
  clusterUtilization: number;
  gpuHoursSaved: number;
  successRate: number;
}

export interface DashboardData {
  overview: DashboardOverview;
  activeJobs: ActiveJob[];
  completedJobs: CompletedJob[];
  clusterNodes: ClusterNode[];
  activityFeed: ActivityItem[];
}

export interface SubmissionPayload {
  projectTitle: string;
  description: string;
  runCommand: string;
  vramMode: 'auto' | 'manual';
  vram: number;
  torchVersion: string;
  cudaVersion: string;
  assetFile: string | null;
}

const API_BASE_URL = (import.meta as any).env?.VITE_API_BASE_URL?.trim();
const USE_API = Boolean(API_BASE_URL);
const MOCK_DELAY = 450;

const mockData: DashboardData = {
  overview: {
    activeJobs: 3,
    queuedJobs: 2,
    runningJobs: 1,
    clusterUtilization: 72,
    gpuHoursSaved: 184,
    successRate: 91,
  },
  activeJobs: [
    {
      id: 'JOB-2105135',
      project: 'ResNet-50 Fine-tuning',
      status: 'Running',
      progress: 45,
      type: 'Training',
      created: '2023-10-24',
      gpu: 'NVIDIA RTX 4090',
      owner: 'Dr. Maya Chen',
      description: 'Fine-tuning an image classification model on a medical imaging dataset.',
      runCommand: 'python train.py --config configs/resnet50.yaml',
      estimatedDuration: '7h 20m',
    },
    {
      id: 'JOB-2105136',
      project: 'BERT Large Pretrain',
      status: 'Queued',
      progress: 0,
      queuePos: 2,
      type: 'NLP',
      created: '2023-10-24',
      gpu: 'NVIDIA A100',
      owner: 'Research Team',
      description: 'Masked language modeling run for domain-specific benchmark pretraining.',
      runCommand: 'python pretrain.py --model bert-large --epochs 12',
      estimatedDuration: '14h 10m',
    },
    {
      id: 'JOB-2105137',
      project: 'Stable Diffusion LoRA',
      status: 'Queued',
      progress: 0,
      queuePos: 5,
      type: 'GenAI',
      created: '2023-10-24',
      gpu: 'NVIDIA RTX 3090',
      owner: 'Design Lab',
      description: 'Low-rank adaptation for product mockup generation with custom imagery.',
      runCommand: 'accelerate launch train_lora.py --dataset ./assets',
      estimatedDuration: '5h 40m',
    },
  ],
  completedJobs: [
    {
      id: '#2105134',
      project: 'BERT Base Uncased',
      date: '2023-10-14',
      status: 'Failed',
      duration: '4h 18m',
      gpu: 'NVIDIA A100',
      score: 'N/A',
      artifact: 'logs/bert-base-uncased.zip',
    },
    {
      id: '#2105133',
      project: 'GAN Style Transfer',
      date: '2023-10-12',
      status: 'Success',
      duration: '2h 09m',
      gpu: 'NVIDIA RTX 4090',
      score: 'FID 18.4',
      artifact: 'artifacts/gan-style-transfer.pt',
    },
    {
      id: '#2105132',
      project: 'LSTM Stock Pred',
      date: '2023-10-10',
      status: 'Success',
      duration: '1h 54m',
      gpu: 'NVIDIA RTX 3090',
      score: 'MAE 0.018',
      artifact: 'artifacts/lstm-stock-pred.pt',
    },
  ],
  clusterNodes: [
    { name: 'gpu-a01', status: 'Online', gpu: 'RTX 4090', utilization: 84, temperature: '69°C' },
    { name: 'gpu-a02', status: 'Online', gpu: 'A100', utilization: 76, temperature: '63°C' },
    { name: 'gpu-b07', status: 'Degraded', gpu: 'RTX 3090', utilization: 59, temperature: '74°C' },
    { name: 'gpu-c11', status: 'Online', gpu: 'L40S', utilization: 91, temperature: '71°C' },
  ],
  activityFeed: [
    { id: 'evt-1', label: 'Job submitted', detail: 'ResNet-50 Fine-tuning moved to Running after environment validation.', time: '3 min ago', tone: 'success' },
    { id: 'evt-2', label: 'Queue updated', detail: 'BERT Large Pretrain reserved slot #2 and is waiting for A100 capacity.', time: '11 min ago', tone: 'info' },
    { id: 'evt-3', label: 'Artifacts synced', detail: 'GAN Style Transfer results were exported to the artifact store.', time: '1 hour ago', tone: 'success' },
    { id: 'evt-4', label: 'GPU warning', detail: 'gpu-b07 is running hotter than the cluster average and is being monitored.', time: '2 hours ago', tone: 'warning' },
  ],
};

let state = cloneDashboardData(mockData);

function cloneDashboardData(data: DashboardData): DashboardData {
  return {
    overview: { ...data.overview },
    activeJobs: data.activeJobs.map((job) => ({ ...job })),
    completedJobs: data.completedJobs.map((job) => ({ ...job })),
    clusterNodes: data.clusterNodes.map((node) => ({ ...node })),
    activityFeed: data.activityFeed.map((item) => ({ ...item })),
  };
}

function withDelay<T>(value: T): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), MOCK_DELAY));
}

function syncOverview(data: DashboardData): DashboardData {
  const queuedJobs = data.activeJobs.filter((job) => job.status === 'Queued').length;
  const runningJobs = data.activeJobs.filter((job) => job.status === 'Running').length;
  const activeJobs = data.activeJobs.length;
  const successRate = Math.round(
    (data.completedJobs.filter((job) => job.status === 'Success').length / Math.max(data.completedJobs.length, 1)) * 100,
  );

  return {
    ...data,
    overview: {
      ...data.overview,
      activeJobs,
      queuedJobs,
      runningJobs,
      successRate,
    },
  };
}

async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      Accept: 'application/json',
    },
  });

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json() as Promise<T>;
}

async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export function isApiMode() {
  return USE_API;
}

export async function getDashboardData(): Promise<DashboardData> {
  if (USE_API) {
    return apiGet<DashboardData>('/dashboard');
  }

  state = syncOverview(state);
  return withDelay(cloneDashboardData(state));
}

export async function submitJob(payload: SubmissionPayload): Promise<DashboardData> {
  if (USE_API) {
    return apiPost<DashboardData>('/jobs', payload);
  }

  const nextIndex = state.activeJobs.length + 1;
  const queuedCount = state.activeJobs.filter((job) => job.status === 'Queued').length;
  const createdJob: ActiveJob = {
    id: `JOB-${2105138 + nextIndex}`,
    project: payload.projectTitle,
    status: 'Queued',
    progress: 0,
    queuePos: queuedCount + 1,
    type: 'Custom',
    created: new Date().toISOString().split('T')[0],
    gpu: payload.vramMode === 'manual' ? `${payload.vram} GB VRAM reserve` : 'Auto-sized GPU slot',
    owner: 'Current user',
    description: payload.description,
    runCommand: payload.runCommand,
    estimatedDuration: payload.vramMode === 'manual' ? 'Estimated after reservation' : 'Estimated after profiling',
  };

  state = syncOverview({
    ...state,
    activeJobs: [createdJob, ...state.activeJobs],
    activityFeed: [
      {
        id: `evt-${Date.now()}`,
        label: 'Mock job created',
        detail: `${payload.projectTitle} was queued with ${payload.torchVersion || 'default'} and ${payload.cudaVersion || 'compatible CUDA'} support.`,
        time: 'just now',
        tone: 'success',
      },
      ...state.activityFeed,
    ],
  });

  return withDelay(cloneDashboardData(state));
}
