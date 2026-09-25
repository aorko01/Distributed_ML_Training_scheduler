import assert from 'node:assert/strict';
import {
  buildJobOutputFilename,
  downloadJobOutputFromApi,
  parseDownloadFilename,
  type DownloadProgress,
  type DownloadRuntime,
} from '../src/services/jobDownload.ts';

const makeHeaders = (entries: Record<string, string> = {}): Headers => {
  const headers = new Headers();
  for (const [key, value] of Object.entries(entries)) headers.set(key, value);
  return headers;
};

const makeRuntime = (): DownloadRuntime & {
  clicked: Array<{ href: string; download: string }>;
  createdBlobs: Blob[];
} => {
  const state = { clicked: [] as Array<{ href: string; download: string }>, createdBlobs: [] as Blob[] };
  return {
    ...state,
    request: () => Promise.reject(new Error('not implemented')),
    createObjectURL: (blob: Blob) => {
      state.createdBlobs.push(blob);
      return 'blob:fake-url';
    },
    revokeObjectURL: () => {},
    createAnchor: () => ({
      href: '',
      download: '',
      click() {
        state.clicked.push({ href: this.href, download: this.download });
      },
      remove() {},
    }),
    appendAnchor: () => {},
  };
};

// --- pure helpers -----------------------------------------------------------

assert.equal(parseDownloadFilename(null, 'fallback.zip'), 'fallback.zip');
assert.equal(
  parseDownloadFilename('attachment; filename="my_run-output.zip"', 'fallback.zip'),
  'my_run-output.zip',
);
assert.equal(
  parseDownloadFilename("attachment; filename*=UTF-8''my%20run-output.zip", 'fallback.zip'),
  'my run-output.zip',
);
assert.equal(buildJobOutputFilename('My Training Run', 'job-1'), 'my_training_run-output.zip');

// --- blob fallback (no readable stream, e.g. mocked responses) --------------

{
  const runtime = makeRuntime();
  const body = new Blob(['abc'], { type: 'application/zip' });
  const response = {
    ok: true,
    status: 200,
    headers: makeHeaders({ 'content-disposition': 'attachment; filename="run-output.zip"' }),
    body: null,
    blob: () => Promise.resolve(body),
  } as unknown as Response;
  runtime.request = () => Promise.resolve(response);

  const seen: DownloadProgress[] = [];
  await downloadJobOutputFromApi(
    { apiBaseUrl: 'http://api.test', id: 'job-1', jobName: 'Run', onProgress: p => seen.push(p) },
    runtime,
  );
  assert.equal(runtime.clicked.length, 1);
  assert.equal(runtime.clicked[0].download, 'run-output.zip');
  assert.ok(seen.length >= 1 && seen[0].phase === 'preparing');
}

// --- streaming path reports preparing -> downloading with byte counts -------

{
  const runtime = makeRuntime();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('hello '));
      controller.enqueue(new TextEncoder().encode('world'));
      controller.close();
    },
  });
  const response = {
    ok: true,
    status: 200,
    headers: makeHeaders({ 'content-disposition': 'attachment; filename="run-output.zip"' }),
    body: stream,
  } as unknown as Response;
  runtime.request = () => Promise.resolve(response);

  const seen: DownloadProgress[] = [];
  await downloadJobOutputFromApi(
    { apiBaseUrl: 'http://api.test/', id: 'job-1', onProgress: p => seen.push({ ...p }) },
    runtime,
  );
  assert.equal(runtime.clicked.length, 1);
  const phases = seen.map(p => p.phase);
  assert.equal(phases[0], 'preparing');
  assert.ok(phases.includes('downloading'), `expected downloading phase, got ${phases}`);
  const last = seen[seen.length - 1];
  assert.equal(last.loadedBytes, 11);
  const assembled = await runtime.createdBlobs[0].text();
  assert.equal(assembled, 'hello world');
}

// --- HTTP errors surface the server detail ----------------------------------

{
  const runtime = makeRuntime();
  const response = {
    ok: false,
    status: 404,
    headers: makeHeaders({ 'content-type': 'application/json' }),
    json: () => Promise.resolve({ detail: 'No output files found for job_id x' }),
    text: () => Promise.resolve(''),
  } as unknown as Response;
  runtime.request = () => Promise.resolve(response);
  await assert.rejects(
    downloadJobOutputFromApi({ apiBaseUrl: 'http://api.test', id: 'missing' }, runtime),
    /No output files found/,
  );
}

console.log('jobDownload tests passed');
