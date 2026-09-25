export interface DownloadAnchor {
  href: string;
  download: string;
  click: () => void;
  remove: () => void;
}

export interface DownloadRuntime {
  request: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
  createObjectURL: (blob: Blob) => string;
  revokeObjectURL: (url: string) => void;
  createAnchor: () => DownloadAnchor;
  appendAnchor: (anchor: DownloadAnchor) => void;
}

export type DownloadPhase = 'preparing' | 'downloading';

export interface DownloadProgress {
  phase: DownloadPhase;
  loadedBytes: number;
  totalBytes: number | null;
}

interface DownloadOptions {
  apiBaseUrl: string;
  id: string;
  jobName?: string;
  token?: string | null;
  signal?: AbortSignal;
  onProgress?: (progress: DownloadProgress) => void;
}

const sanitizeFilename = (candidate: string, fallback: string): string => {
  const basename = candidate.split(/[\\/]/).pop()?.trim() ?? '';
  const withoutControls = Array.from(basename, character => {
    const codePoint = character.codePointAt(0) ?? 0;
    return codePoint < 32 || codePoint === 127 ? '_' : character;
  }).join('');
  const safe = withoutControls
    .replace(/["<>:|?*]/g, '_')
    .slice(0, 255);
  return safe && safe !== '.' && safe !== '..' ? safe : fallback;
};

export const parseDownloadFilename = (
  disposition: string | null,
  fallback: string,
): string => {
  const safeFallback = sanitizeFilename(fallback, 'job-output.zip');
  if (!disposition) return safeFallback;

  const utf8Match = disposition.match(/filename\*\s*=\s*(?:"?UTF-8''([^";]+)"?)/i);
  if (utf8Match?.[1]) {
    let decoded = utf8Match[1].trim();
    try {
      decoded = decodeURIComponent(decoded);
    } catch {
      // Use the undecoded value, but still sanitize it below.
    }
    return sanitizeFilename(decoded, safeFallback);
  }

  const quotedMatch = disposition.match(/filename\s*=\s*"([^"]+)"/i);
  if (quotedMatch?.[1]) return sanitizeFilename(quotedMatch[1], safeFallback);

  const tokenMatch = disposition.match(/filename\s*=\s*([^;]+)/i);
  if (tokenMatch?.[1]) return sanitizeFilename(tokenMatch[1], safeFallback);
  return safeFallback;
};

export const buildJobOutputFilename = (jobName: string, jobId: string): string => {
  const rawBase = (jobName || jobId || 'job').trim() || jobId || 'job';
  const base = rawBase
    .toLowerCase()
    .replace(/\s+/g, '_')
    .replace(/[^a-z0-9._-]/g, '_')
    .replace(/^\.+/, '')
    .slice(0, 100) || 'job';
  return `${base}-output.zip`;
};

const browserRuntime = (): DownloadRuntime => ({
  request: (input, init) => fetch(input, init),
  createObjectURL: blob => URL.createObjectURL(blob),
  revokeObjectURL: url => URL.revokeObjectURL(url),
  createAnchor: () => document.createElement('a'),
  appendAnchor: anchor => document.body.appendChild(anchor as HTMLAnchorElement),
});

const errorMessage = async (response: Response): Promise<string> => {
  let message = `Download failed with status ${response.status}`;
  try {
    const contentType = response.headers.get('content-type') ?? '';
    if (contentType.includes('application/json')) {
      const body = (await response.json()) as { detail?: unknown; error?: unknown };
      if (typeof body.detail === 'string' && body.detail) return body.detail;
      if (typeof body.error === 'string' && body.error) return body.error;
    } else {
      const text = await response.text();
      if (text) message = text.slice(0, 500);
    }
  } catch {
    // Keep the status-based fallback when an error body cannot be parsed.
  }
  return message;
};

const parseTotalBytes = (response: Response): number | null => {
  const raw = response.headers.get('content-length');
  if (!raw) return null;
  const total = Number.parseInt(raw, 10);
  return Number.isFinite(total) && total >= 0 ? total : null;
};

interface SavePickerWritable {
  write: (chunk: Uint8Array) => Promise<void>;
  close: () => Promise<void>;
  abort?: () => Promise<void>;
}

interface SavePickerHandle {
  createWritable: () => Promise<SavePickerWritable>;
}

const pickSaveFile = async (suggestedName: string): Promise<SavePickerHandle | null> => {
  try {
    if (typeof window === 'undefined') return null;
    const w = window as unknown as {
      showSaveFilePicker?: (options?: {
        suggestedName?: string;
        types?: Array<{ description?: string; accept: Record<string, string[]> }>;
      }) => Promise<SavePickerHandle>;
    };
    if (typeof w.showSaveFilePicker !== 'function') return null;
    return await w.showSaveFilePicker({
      suggestedName,
      types: [{ description: 'ZIP archive', accept: { 'application/zip': ['.zip'] } }],
    });
  } catch {
    // The picker is unavailable or was dismissed before streaming started;
    // fall back to the in-memory download path below.
    return null;
  }
};

/** Legacy path for mocked responses without a readable stream (tests, old browsers). */
const downloadAsBlob = async (
  response: Response,
  filename: string,
  runtime: DownloadRuntime,
): Promise<void> => {
  const objectUrl = runtime.createObjectURL(await response.blob());
  let anchor: DownloadAnchor | undefined;
  try {
    anchor = runtime.createAnchor();
    anchor.href = objectUrl;
    anchor.download = filename;
    runtime.appendAnchor(anchor);
    anchor.click();
  } finally {
    anchor?.remove();
    runtime.revokeObjectURL(objectUrl);
  }
};

/**
 * Stream the archive to disk chunk by chunk instead of buffering the whole
 * response with ``response.blob()``.
 *
 * Large checkpoints previously appeared stuck on "Preparing…": the server
 * assembled the entire ZIP before the first byte, and then the browser held
 * the whole file in RAM. Streaming flips to the "downloading" phase on the
 * first byte, reports live byte counts via ``onProgress``, and — where the
 * File System Access API exists (Chrome/Edge) — writes straight to disk so
 * multi-gigabyte outputs never blow up tab memory.
 */
export const downloadJobOutputFromApi = async (
  options: DownloadOptions,
  runtime: DownloadRuntime = browserRuntime(),
): Promise<void> => {
  const headers: Record<string, string> = {};
  if (options.token) headers.Authorization = `Bearer ${options.token}`;

  const report = (progress: DownloadProgress): void => {
    try {
      options.onProgress?.(progress);
    } catch {
      // Progress listeners must never break the download itself.
    }
  };

  const baseUrl = options.apiBaseUrl.replace(/\/$/, '');
  report({ phase: 'preparing', loadedBytes: 0, totalBytes: null });
  const response = await runtime.request(
    `${baseUrl}/jobs/${encodeURIComponent(options.id)}/output/download`,
    { headers, signal: options.signal },
  );
  if (!response.ok) throw new Error(await errorMessage(response));

  const fallback = buildJobOutputFilename(options.jobName ?? '', options.id);
  const filename = parseDownloadFilename(
    response.headers.get('content-disposition'),
    fallback,
  );
  const totalBytes = parseTotalBytes(response);

  const body = response.body as ReadableStream<Uint8Array> | null | undefined;
  if (!body || typeof body.getReader !== 'function') {
    await downloadAsBlob(response, filename, runtime);
    report({ phase: 'downloading', loadedBytes: totalBytes ?? 0, totalBytes });
    return;
  }

  const reader = body.getReader();
  let loadedBytes = 0;
  let phase: DownloadPhase = 'preparing';
  const markDownloading = (): void => {
    if (phase !== 'downloading') {
      phase = 'downloading';
      report({ phase, loadedBytes, totalBytes });
    }
  };

  // Preferred path: stream straight to disk, bypassing tab memory entirely.
  const fileHandle = await pickSaveFile(filename);
  if (fileHandle) {
    const writable = await fileHandle.createWritable();
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (options.signal?.aborted) throw new DOMException('Download cancelled', 'AbortError');
        if (done) break;
        if (value && value.byteLength > 0) {
          await writable.write(value);
          loadedBytes += value.byteLength;
          markDownloading();
          report({ phase, loadedBytes, totalBytes });
        }
      }
      await writable.close();
      return;
    } catch (err) {
      try {
        if (typeof writable.abort === 'function') await writable.abort();
        else await writable.close();
      } catch {
        // Ignore secondary cleanup failures; the original error matters.
      }
      try {
        reader.releaseLock();
      } catch {
        // Reader may already be closed after a network failure.
      }
      throw err;
    }
  }

  // Fallback path (Firefox/Safari, picker dismissed): accumulate chunks with
  // live progress, then trigger the classic anchor download.
  const chunks: Uint8Array[] = [];
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (options.signal?.aborted) throw new DOMException('Download cancelled', 'AbortError');
      if (done) break;
      if (value && value.byteLength > 0) {
        chunks.push(value);
        loadedBytes += value.byteLength;
        markDownloading();
        report({ phase, loadedBytes, totalBytes });
      }
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // Reader may already be closed after a network failure.
    }
  }

  const objectUrl = runtime.createObjectURL(new Blob(chunks as BlobPart[], { type: 'application/zip' }));
  let anchor: DownloadAnchor | undefined;
  try {
    anchor = runtime.createAnchor();
    anchor.href = objectUrl;
    anchor.download = filename;
    runtime.appendAnchor(anchor);
    anchor.click();
  } finally {
    anchor?.remove();
    runtime.revokeObjectURL(objectUrl);
  }
};
