export interface ClusterStats {
  queueLength: number;
  gpuHoursUsed: number;
  activeJobs: number;
  totalNodes: number;
}

export const fetchClusterStats = async (): Promise<ClusterStats> => {
  return new Promise((resolve) => {
    setTimeout(() => {
      resolve({
        queueLength: 14,
        gpuHoursUsed: 1245.5,
        activeJobs: 3,
        totalNodes: 8
      });
    }, 300);
  });
};
