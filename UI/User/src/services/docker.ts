import { api } from './api';

export interface PytorchVersion {
  version: string;
  cudaVersions: string[];
}

export const fetchPytorchVersions = async (): Promise<PytorchVersion[]> => {
  return api.get<PytorchVersion[]>('/docker/pytorch-tags');
};
