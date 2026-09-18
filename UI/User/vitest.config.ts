import { defineConfig } from 'vitest/config';
export default defineConfig({ esbuild: { jsx: 'automatic' }, test: { environment: 'jsdom', exclude: ['test/jobDownload.test.ts', '**/node_modules/**'] } });
