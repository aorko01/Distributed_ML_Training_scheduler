import type { Runtime, Workspace } from '../../services/interactive';

export type ConnPhase = 'idle' | 'loading' | 'connecting' | 'connected' | 'reconnecting' | 'disconnected' | 'fatal';
export type PtyUiState = 'closed' | 'opening' | 'open' | 'closing' | 'exited';
export interface ExplorerNode { path: string; name: string; type: 'file' | 'directory' | 'symlink' | 'unsupported'; expanded: boolean; loading: boolean; error: string | null; children: string[] | null; nextCursor: number | null }
export interface OpenFile { path: string; version: string; savedText: string; conflict: null | { serverText: string; serverVersion: string }; saving: boolean; error: string | null }
export interface Notice { id: string; kind: 'info' | 'success' | 'error'; text: string }
export interface WorkspaceSnapshot {
  workspace: Workspace | null; runtime: Runtime | null;
  phase: ConnPhase; message: string; closeCode: number | null; attempt: number;
  nodes: Record<string, ExplorerNode>; roots: string[]; selected: string | null;
  tabs: string[]; active: string | null; files: Record<string, OpenFile>;
  dirtyCount: number; readOnly: boolean; pty: PtyUiState; ptyExit: { code: number; reason: string } | null; ptyHadSession: boolean;
  notices: Notice[]; conflictPath: string | null; caps: { textFileLimit: number } | null;
}
export const ROOT_KEY = '';
export function parentPath(path: string): string { const i = path.lastIndexOf('/'); return i < 0 ? '' : path.slice(0, i); }
export function baseName(path: string): string { const i = path.lastIndexOf('/'); return i < 0 ? path : path.slice(i + 1); }
export function isValidName(name: string): boolean { return name.length > 0 && name.length <= 255 && !name.includes('/') && !name.includes('\\') && !name.includes('\u0000') && name !== '.' && name !== '..'; }
export function joinPath(dir: string, name: string): string { return dir ? `${dir}/${name}` : name; }
export function languageFor(path: string): string {
  const lower = path.toLowerCase();
  if (lower.endsWith('.py')) return 'python';
  if (lower.endsWith('.ts') || lower.endsWith('.tsx')) return 'typescript';
  if (lower.endsWith('.js') || lower.endsWith('.jsx')) return 'javascript';
  if (lower.endsWith('.json')) return 'json';
  if (lower.endsWith('.yml') || lower.endsWith('.yaml')) return 'yaml';
  if (lower.endsWith('.md')) return 'markdown';
  if (lower.endsWith('.sh')) return 'shell';
  if (lower.endsWith('.toml') || lower.endsWith('.ini') || lower.endsWith('.cfg')) return 'ini';
  if (lower.endsWith('.txt') || lower.endsWith('.log')) return 'plaintext';
  const base = baseName(lower);
  if (base === 'dockerfile' || base.startsWith('dockerfile.')) return 'dockerfile';
  if (base === 'requirements.txt' || base === 'makefile') return 'plaintext';
  return 'plaintext';
}

export interface OpenFile { path: string; version: string; savedText: string; conflict: null | { serverText: string; serverVersion: string }; saving: boolean; error: string | null }
