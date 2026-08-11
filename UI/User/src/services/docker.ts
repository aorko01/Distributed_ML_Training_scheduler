// Mock data for Docker Hub PyTorch tags
export interface PytorchVersion {
  version: string;
  cudaVersions: string[];
}

const mockTags = [
  { pytorch: '2.3.1', cuda: ['12.1', '11.8'] },
  { pytorch: '2.3.0', cuda: ['12.1', '11.8'] },
  { pytorch: '2.2.2', cuda: ['12.1', '11.8'] },
  { pytorch: '2.2.1', cuda: ['12.1', '11.8'] },
  { pytorch: '2.2.0', cuda: ['12.1', '11.8'] },
  { pytorch: '2.1.2', cuda: ['12.1', '11.8'] },
  { pytorch: '2.1.1', cuda: ['12.1', '11.8'] },
  { pytorch: '2.1.0', cuda: ['12.1', '11.8', '11.1'] },
  { pytorch: '2.0.1', cuda: ['11.8', '11.7'] },
  { pytorch: '2.0.0', cuda: ['11.8', '11.7'] },
  { pytorch: '1.13.1', cuda: ['11.7', '11.6'] },
];

export const fetchPytorchVersions = async (): Promise<PytorchVersion[]> => {
  // Simulate network delay
  return new Promise((resolve) => {
    setTimeout(() => {
      resolve(mockTags.map(tag => ({
        version: tag.pytorch,
        cudaVersions: tag.cuda
      })));
    }, 400);
  });
};
