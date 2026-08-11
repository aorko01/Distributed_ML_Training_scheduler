import { api } from './api';

export interface ClusterStats {
  queueLength: number;
  gpuHoursUsed: number;
  jobCount: number;
  totalNodes: number;
}

const DUMMY_GPU_HOURS = 1245.5;
const DUMMY_TOTAL_NODES = 8;

export const fetchClusterStats = async (): Promise<ClusterStats> => {
  try {
    const [queueRes, countRes] = await Promise.all([
      api.get<{ queue_length?: number }>('/jobs/queue_length'),
      api.get<{ count?: number }>('/jobs/mine/count'),
    ]);

    return {
      queueLength: queueRes.queue_length ?? 0,
      gpuHoursUsed: DUMMY_GPU_HOURS,
      jobCount: countRes.count ?? 0,
      totalNodes: DUMMY_TOTAL_NODES,
    };
  } catch {
    return {
      queueLength: 14,
      gpuHoursUsed: DUMMY_GPU_HOURS,
      jobCount: 3,
      totalNodes: DUMMY_TOTAL_NODES,
    };
  }
};
