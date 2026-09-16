import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildJobOutputFilename,
  downloadJobOutputFromApi,
  parseDownloadFilename,
  type DownloadAnchor,
  type DownloadRuntime,
} from '../src/services/jobDownload.ts';


const makeRuntime = (response: Response) => {
  const calls = {
    request: [] as Array<{ input: string; init?: RequestInit }>,
    createdBlob: undefined as Blob | undefined,
    revoked: [] as string[],
    appended: 0,
    clicked: 0,
    removed: 0,
  };
  const anchor: DownloadAnchor = {
    href: '',
    download: '',
    click: () => { calls.clicked += 1; },
    remove: () => { calls.removed += 1; },
  };
  const runtime: DownloadRuntime = {
    request: async (input, init) => {
      calls.request.push({ input: String(input), init });
      return response;
    },
    createObjectURL: blob => {
      calls.createdBlob = blob;
      return 'blob:test-output';
    },
    revokeObjectURL: url => { calls.revoked.push(url); },
    createAnchor: () => anchor,
    appendAnchor: () => { calls.appended += 1; },
  };
  return { runtime, anchor, calls };
};


test('parses RFC 5987 and quoted filenames safely', () => {
  assert.equal(
    parseDownloadFilename(
      "attachment; filename*=UTF-8''training%20run-output.zip",
      'fallback.zip',
    ),
    'training run-output.zip',
  );
  assert.equal(
    parseDownloadFilename('attachment; filename="result.zip"', 'fallback.zip'),
    'result.zip',
  );
  assert.equal(
    parseDownloadFilename(
      "attachment; filename*=UTF-8''..%2F..%2Fevil.zip",
      'fallback.zip',
    ),
    'evil.zip',
  );
});

test('builds a bounded filesystem-safe fallback filename', () => {
  assert.equal(buildJobOutputFilename('My Training Run', 'job-1'), 'my_training_run-output.zip');
  assert.doesNotMatch(buildJobOutputFilename('../../evil', 'job-1'), /[\\/]/);
  assert.equal(buildJobOutputFilename('', 'job-1'), 'job-1-output.zip');
  assert.ok(buildJobOutputFilename('x'.repeat(200), 'job-1').length <= 111);
});

test('downloads with bearer auth and always cleans up browser resources', async () => {
  const body = new Blob(['archive-bytes'], { type: 'application/zip' });
  const response = new Response(body, {
    status: 200,
    headers: {
      'content-type': 'application/zip',
      'content-disposition': 'attachment; filename="server-output.zip"',
    },
  });
  const { runtime, anchor, calls } = makeRuntime(response);

  await downloadJobOutputFromApi(
    {
      apiBaseUrl: 'http://scheduler.test/',
      id: 'job/with slash',
      jobName: 'Training',
      token: 'secret-token',
    },
    runtime,
  );

  assert.equal(
    calls.request[0].input,
    'http://scheduler.test/jobs/job%2Fwith%20slash/output/download',
  );
  assert.deepEqual(calls.request[0].init?.headers, {
    Authorization: 'Bearer secret-token',
  });
  assert.equal(anchor.download, 'server-output.zip');
  assert.equal(anchor.href, 'blob:test-output');
  assert.equal(calls.createdBlob?.size, body.size);
  assert.equal(calls.appended, 1);
  assert.equal(calls.clicked, 1);
  assert.equal(calls.removed, 1);
  assert.deepEqual(calls.revoked, ['blob:test-output']);
});

test('surfaces a JSON API error without creating a download', async () => {
  const response = Response.json(
    { detail: 'An output object disappeared while downloading' },
    { status: 502 },
  );
  const { runtime, calls } = makeRuntime(response);

  await assert.rejects(
    downloadJobOutputFromApi(
      { apiBaseUrl: 'http://scheduler.test', id: 'job-1' },
      runtime,
    ),
    /An output object disappeared while downloading/,
  );
  assert.equal(calls.createdBlob, undefined);
  assert.equal(calls.appended, 0);
});

test('revokes the object URL even when the browser click fails', async () => {
  const { runtime, anchor, calls } = makeRuntime(new Response('zip'));
  anchor.click = () => { throw new Error('click blocked'); };

  await assert.rejects(
    downloadJobOutputFromApi(
      { apiBaseUrl: 'http://scheduler.test', id: 'job-1' },
      runtime,
    ),
    /click blocked/,
  );
  assert.equal(calls.removed, 1);
  assert.deepEqual(calls.revoked, ['blob:test-output']);
});
