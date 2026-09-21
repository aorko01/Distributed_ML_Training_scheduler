// Production Xterm.js adapter (local dependency, no CDN).
// Instantiated lazily only when the terminal host is mounted and measurable.
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
// Xterm ships without its layout CSS applied by default; without this import
// the viewport/canvas render as a small unstyled box.
import '@xterm/xterm/css/xterm.css';
import type { TermHandle } from './adapters';

export interface XtermHandle extends TermHandle {
  dispose(): void;
  fit(): void;
  onData(cb: (data: string) => void): void;
}

export function createXterm(host: HTMLElement, opts: { fontSize?: number } = {}): XtermHandle | null {
  if (!host.isConnected) return null;
  const rect = host.getBoundingClientRect();
  if (rect.width < 2 || rect.height < 2) return null;
  const term = new Terminal({
    theme: {
      background: '#080b10',
      foreground: '#c7d2e2',
      cursor: '#39a9ff',
      cursorAccent: '#080b10',
      selectionBackground: 'rgba(57, 169, 255, 0.35)',
      black: '#080b10',
      white: '#c7d2e2',
    },
    fontFamily: "'JetBrains Mono', Menlo, Consolas, monospace",
    fontSize: opts.fontSize ?? 13,
    allowTransparency: false,
    scrollback: 5000,
  });
  const fit = new FitAddon();
  term.loadAddon(fit);
  term.open(host);
  try { fit.fit(); } catch { /* measure again on resize */ }
  return {
    write: (data) => {
      // Xterm accepts raw bytes semantics via string; callers pass UTF-8.
      // Never force convertEol: preserve real PTY carriage-return behaviour.
      term.write(typeof data === 'string' ? data : new TextDecoder().decode(data));
    },
    clear: () => term.clear(),
    focus: () => term.focus(),
    cols: () => term.cols,
    rows: () => term.rows,
    paste: (text) => term.paste(text),
    dispose: () => { try { fit.dispose(); } catch { /* ignore */ } try { term.dispose(); } catch { /* ignore */ } },
    fit: () => { try { fit.fit(); } catch { /* ignore */ } },
    onData: (cb) => { term.onData(cb); },
  };
}
