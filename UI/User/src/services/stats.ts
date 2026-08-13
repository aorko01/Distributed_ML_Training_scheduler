import { api } from './api';

export interface ClusterStats {
  queueLength: number;
  gpuHoursUsed: number;
  jobCount: number;
  totalNodes: number;
}

export const fetchClusterStats = async (): Promise<ClusterStats> => {
  try {
    const [queueRes, countRes, gpuHoursRes, totalGpusRes] = await Promise.all([
      api.get<{ queue_length?: number }>('/jobs/queue_length'),
      api.get<{ count?: number }>('/jobs/mine/count'),
      api.get<{ gpu_hours?: number }>('/jobs/mine/gpu_hours'),
      api.get<{ total_gpus?: number }>('/workers/total_gpus'),
    ]);

    return {
      queueLength: queueRes.queue_length ?? 0,
      gpuHoursUsed: gpuHoursRes.gpu_hours ?? 0,
      jobCount: countRes.count ?? 0,
      totalNodes: totalGpusRes.total_gpus ?? 0,
    };
  } catch {
    return {
      queueLength: 0,
      gpuHoursUsed: 0,
      jobCount: 0,
      totalNodes: 0,
    };
  }
};