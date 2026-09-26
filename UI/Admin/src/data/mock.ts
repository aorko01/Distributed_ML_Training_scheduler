// Shared Admin UI types. (Formerly mock.ts: all dummy datasets were removed
// once every page was wired to live Scheduler data. Only types remain.)
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
  executionDraining?: boolean;
  adminRestricted?: boolean;
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

export interface ClusterOverview {
  nodesOnline: number;
  nodesTotal: number;
  clusterLoad: number;
  queueDepth: number;
  gpusAllocated: number;
  gpusTotal: number;
  distribution: ResourceDistribution;
}
