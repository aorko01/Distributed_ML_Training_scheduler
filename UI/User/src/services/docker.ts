import { api } from './api';

export interface CudaVariant {
  cuda: string;
  cudnn: string;
  tag: string;
}

export interface PytorchVersion {
  version: string;
  cudaVersions: CudaVariant[];
}

export const fetchPytorchVersions = async (): Promise<PytorchVersion[]> => {
  return api.get<PytorchVersion[]>('/docker/pytorch-tags');
};
