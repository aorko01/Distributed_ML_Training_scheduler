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

interface DownloadOptions {
  apiBaseUrl: string;
  id: string;
  jobName?: string;
  token?: string | null;
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

export const downloadJobOutputFromApi = async (
  options: DownloadOptions,
  runtime: DownloadRuntime = browserRuntime(),
): Promise<void> => {
  const headers: Record<string, string> = {};
  if (options.token) headers.Authorization = `Bearer ${options.token}`;

  const baseUrl = options.apiBaseUrl.replace(/\/$/, '');
  const response = await runtime.request(
    `${baseUrl}/jobs/${encodeURIComponent(options.id)}/output/download`,
    { headers },
  );
  if (!response.ok) throw new Error(await errorMessage(response));

  const fallback = buildJobOutputFilename(options.jobName ?? '', options.id);
  const filename = parseDownloadFilename(
    response.headers.get('content-disposition'),
    fallback,
  );
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
