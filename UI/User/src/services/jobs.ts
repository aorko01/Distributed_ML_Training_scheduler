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

export const submitJob = async (jobData: Omit<Job, 'id' | 'status' | 'submittedAt' | 'gpuHours'>): Promise<Job> => {
  return new Promise((resolve) => {
    setTimeout(() => {
      const newJob: Job = {
        ...jobData,
        id: `job-${Math.floor(Math.random() * 1000) + 200}`,
        status: 'Building',
        submittedAt: new Date().toISOString(),
        gpuHours: 0,
        queuePosition: 14
      };
      mockJobs = [newJob, ...mockJobs];
      resolve(newJob);
    }, 800);
  });
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
