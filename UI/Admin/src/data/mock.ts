export type NodeStatus = 'online' | 'offline' | 'draining';
export type NodeSortKey = 'name' | 'load' | 'mem' | 'gpus' | 'vram' | 'running';
export type NodeFilter = 'all' | NodeStatus;

export interface ClusterNode {
  id: string;
  name: string;
  ip: string;
  gpuModel: string;
  gpuCount: number;
  vramPerGpu: number;
  availableVram: number;
  status: NodeStatus;
  load: number;
  gpuLoad: number;
  cpuLoad: number;
  mem: number;
  runningJobs: number;
  sshPort: number;
}

export interface ThroughputPoint {
  label: string;
  jobs: number;
}

export type ThroughputPeriod = 'daily' | 'weekly' | 'monthly' | 'yearly';

export interface ResourceDistribution {
  batch: number;
  experimentation: number;
  idle: number;
}

export type UserRole = 'admin' | 'researcher' | 'user';
export type UserStatus = 'active' | 'disabled';
export type UserSortKey = 'name' | 'role' | 'jobs' | 'gpuHours' | 'created';
export type UserFilter = 'all' | UserRole | UserStatus;

export interface ManagedUser {
  id: string;
  username: string;
  name: string;
  email: string;
  role: UserRole;
  jobsCount: number;
  gpuHours: number;
  status: UserStatus;
  created: string;
}

export type PriorityLevel = 'high' | 'medium' | 'low';
export type PriorityRequestStatus = 'pending' | 'approved' | 'denied' | 'none';

export interface QueueJob {
  id: string;
  name: string;
  user: string;
  priority: PriorityLevel;
  gpuRequested: number;
  submittedAt: string;
  priorityRequest: PriorityRequestStatus;
}

export interface ClusterOverview {
  nodesOnline: number;
  nodesTotal: number;
  clusterLoad: number;
  queueDepth: number;
  gpusAllocated: number;
  gpusTotal: number;
  distribution: ResourceDistribution;
}

const NODE_POOLS: { ipBase: string; gpu: string; vram: number; count: number; nodes: number }[] = [
  { ipBase: '10.0.1.', gpu: 'NVIDIA A100 80GB', vram: 80, count: 8, nodes: 6 },
  { ipBase: '10.0.2.', gpu: 'NVIDIA H100 80GB', vram: 80, count: 8, nodes: 4 },
  { ipBase: '10.0.3.', gpu: 'NVIDIA A100 40GB', vram: 40, count: 4, nodes: 5 },
  { ipBase: '10.0.4.', gpu: 'NVIDIA RTX A6000 48GB', vram: 48, count: 2, nodes: 3 },
  { ipBase: '10.0.5.', gpu: 'NVIDIA A40 48GB', vram: 48, count: 4, nodes: 4 },
  { ipBase: '10.0.6.', gpu: 'NVIDIA A100 80GB', vram: 80, count: 8, nodes: 3 },
  { ipBase: '10.0.7.', gpu: 'NVIDIA RTX 4090 24GB', vram: 24, count: 2, nodes: 5 },
];

const NODE_NAMES = ['ares', 'atlas', 'boron', 'carbon', 'cobalt', 'cygnus', 'draco', 'gauss', 'helios', 'ion', 'kepler', 'laplace', 'lyra', 'nebula', 'orion', 'pulsar', 'quantum', 'rigel', 'sirius', 'tensor', 'vega', 'volt', 'xenon', 'yottabyte', 'zephyr', 'astro', 'core', 'delta', 'epsilon', 'fusion'];

const FIRST_NAMES = ['Alice', 'Bob', 'Carol', 'Dave', 'Eve', 'Frank', 'Grace', 'Henry', 'Ivy', 'Jack', 'Kara', 'Liam', 'Mia', 'Noah', 'Olivia', 'Priya', 'Quinn', 'Raj', 'Sofia', 'Tom'];
const LAST_NAMES = ['Chen', 'Patel', 'Kim', 'Okafor', 'Silva', 'Novak', 'Ali', 'Wang', 'Haddad', 'Dubois', 'Fischer', 'Reyes', 'Mensah', 'Larsen', 'Costa', 'Berg', 'Nakamura', 'Osei', 'Moreau', 'Iqbal'];

export const clusterOverview: ClusterOverview = {
  nodesOnline: 24,
  nodesTotal: 30,
  clusterLoad: 72,
  queueDepth: 18,
  gpusAllocated: 89,
  gpusTotal: 148,
  distribution: {
    batch: 46,
    experimentation: 26,
    idle: 28,
  },
};

export const nodes: ClusterNode[] = NODE_POOLS.flatMap((pool, poolIdx) =>
  Array.from({ length: pool.nodes }, (_, i) => {
    const global = poolIdx * 100 + i;
    const status: NodeStatus =
      i === 0 && poolIdx === 1 ? 'offline'
      : i === 0 && poolIdx === 4 ? 'draining'
      : Math.random() < 0.06 ? 'draining'
      : 'online';
    const load = status === 'offline' ? 0 : Math.min(100, Math.round(25 + Math.random() * 70));
    const mem = status === 'offline' ? 0 : Math.min(100, Math.round(15 + Math.random() * 80));
    const runningJobs = status === 'offline' ? 0 : Math.max(0, Math.round(load / 28));
    const availableVram =
      status === 'offline' ? 0 : Math.max(0, Math.round(pool.vram * (1 - (load / 100) * 0.9)));
    return {
      id: `node-${global}`,
      name: `node-${NODE_NAMES[(global + poolIdx * 5) % NODE_NAMES.length]}`,
      ip: `${pool.ipBase}${i + 10}`,
      gpuModel: pool.gpu,
      gpuCount: pool.count,
      vramPerGpu: pool.vram,
      availableVram,
      status,
      load,
      gpuLoad: load,
      cpuLoad: status === 'offline' ? 0 : Math.min(100, Math.round(10 + Math.random() * 50)),
      mem,
      runningJobs,
      sshPort: 22,
    };
  }),
);

const THROUGHPUT_BASE: Record<ThroughputPeriod, ThroughputPoint[]> = {
  daily: [
    { label: '00:00', jobs: 2 },
    { label: '03:00', jobs: 4 },
    { label: '06:00', jobs: 6 },
    { label: '09:00', jobs: 11 },
    { label: '12:00', jobs: 14 },
    { label: '15:00', jobs: 12 },
    { label: '18:00', jobs: 17 },
    { label: '21:00', jobs: 9 },
  ],
  weekly: [
    { label: 'Mon', jobs: 42 },
    { label: 'Tue', jobs: 55 },
    { label: 'Wed', jobs: 48 },
    { label: 'Thu', jobs: 61 },
    { label: 'Fri', jobs: 57 },
    { label: 'Sat', jobs: 33 },
    { label: 'Sun', jobs: 29 },
  ],
  monthly: [
    { label: 'Week 1', jobs: 180 },
    { label: 'Week 2', jobs: 212 },
    { label: 'Week 3', jobs: 195 },
    { label: 'Week 4', jobs: 238 },
  ],
  yearly: [
    { label: 'Jan', jobs: 620 },
    { label: 'Feb', jobs: 700 },
    { label: 'Mar', jobs: 812 },
    { label: 'Apr', jobs: 940 },
    { label: 'May', jobs: 1055 },
    { label: 'Jun', jobs: 980 },
    { label: 'Jul', jobs: 1120 },
    { label: 'Aug', jobs: 1240 },
    { label: 'Sep', jobs: 1180 },
    { label: 'Oct', jobs: 1310 },
    { label: 'Nov', jobs: 1425 },
    { label: 'Dec', jobs: 1510 },
  ],
};

export const throughput: Record<ThroughputPeriod, ThroughputPoint[]> = THROUGHPUT_BASE;

export const users: ManagedUser[] = Array.from({ length: 24 }, (_, i) => {
  const firstName = FIRST_NAMES[i % FIRST_NAMES.length];
  const lastName = LAST_NAMES[(i * 3) % LAST_NAMES.length];
  const role: UserRole = i === 0 ? 'admin' : i % 5 === 0 ? 'researcher' : 'user';
  const status: UserStatus = i % 8 === 0 ? 'disabled' : 'active';
  return {
    id: `user-${i}`,
    username: `${firstName.toLowerCase()}${lastName.toLowerCase()}`,
    name: `${firstName} ${lastName}`,
    email: `${firstName.toLowerCase()}${lastName.toLowerCase()}@example.com`,
    role,
    jobsCount: role === 'admin' ? 2 : 5 + ((i * 7) % 60),
    gpuHours: role === 'admin' ? 0 : Math.round((2 + (i * 13) % 340) * 10) / 10,
    status,
    created: new Date(Date.UTC(2024, i % 12, 2 + (i % 26))).toISOString().slice(0, 10),
  };
});

const JOB_NAMES = ['llm-finetune', 'resnet50-train', 'gpt-lora', 'vision-detect', 'text-embed', 'diffusion-gen', 'bert-pretrain', 'yolo-tune', 'seq2seq-train', 'gan-train', 'sentiment-ft', 'audio-transcribe'];
const JOB_USERS = ['ali', 'sophia', 'raj', 'noah', 'priya', 'jack', 'kara', 'liam', 'mia', 'tom'];

export const queueJobs: QueueJob[] = Array.from({ length: 18 }, (_, i) => {
  const priority: PriorityLevel = i % 4 === 0 ? 'high' : i % 3 === 0 ? 'medium' : 'low';
  const request: PriorityRequestStatus =
    i === 1 ? 'pending' : i === 3 ? 'pending' : i === 7 ? 'approved' : i === 9 ? 'denied' : 'none';
  const gpuRequested = priority === 'high' ? 8 : priority === 'medium' ? 4 : [1, 2][i % 2];
  const hoursAgo = 1 + i * 3;
  return {
    id: `job-${100 + i}`,
    name: `${JOB_NAMES[i % JOB_NAMES.length]}-${100 + i}`,
    user: JOB_USERS[i % JOB_USERS.length],
    priority,
    gpuRequested,
    submittedAt: new Date(Date.now() - hoursAgo * 3600_000).toISOString(),
    priorityRequest: request,
  };
});
