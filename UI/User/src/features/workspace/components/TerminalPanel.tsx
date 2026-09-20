import { useEffect, useRef } from 'react';
import { Maximize2, Minimize2, Plus, Trash2, X, Square } from 'lucide-react';
import type { PtyUiState } from '../workspaceTypes';
import type { TermHandle } from '../adapters';

interface Props {
  pty: PtyUiState;
  exit: { code: number; reason: string } | null;
  collapsed: boolean;
  maximized: boolean;
  height?: number;
  onOpen: () => void;
  onClosePty: () => void;
  onClear: () => void;
  onToggleCollapse: () => void;
  onToggleMax: () => void;
  onInput: (data: string) => void;
  onResize: (cols: number, rows: number) => void;
  register: (h: TermHandle | null) => void;
}

// Xterm surface with a DOM-lines fallback. Production browsers lazily mount
// the real Xterm.js terminal (local bundle, FitAddon, debounced resize);
// jsdom/component tests use the lightweight fallback through the same
// TermHandle interface so transport behaviour stays under test.
export function TerminalPanel({ pty, exit, collapsed, maximized, height, onOpen, onClosePty, onClear, onToggleCollapse, onToggleMax, onInput, onResize, register }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const linesRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const handleRef = useRef<TermHandle | null>(null);
  const label = pty === 'open' ? 'running' : pty === 'opening' ? 'starting' : pty === 'closing' ? 'closing' : pty === 'exited' ? `exited (${exit?.code ?? 0})` : 'closed';
  const onInputRef = useRef(onInput);
  onInputRef.current = onInput;
  const onResizeRef = useRef(onResize);
  onResizeRef.current = onResize;

  useEffect(() => {
    const host = hostRef.current;
    const lines = linesRef.current;
    const isJsdom = typeof navigator !== 'undefined' && navigator.userAgent.includes('jsdom');
    if (!host || collapsed) { register(null); return; }
    let cancelled = false;
    let xterm: { dispose(): void; fit(): void; onData(cb: (d: string) => void): void } | null = null;
    if (!isJsdom && host.isConnected) {
      void import('../xtermAdapter.js').then((m) => {
        if (cancelled || !host.isConnected) return;
        const rect = host.getBoundingClientRect();
        if (rect.width < 2 || rect.height < 2) return;
        const h = m.createXterm(host);
        if (!h || cancelled) { try { h?.dispose(); } catch { /* ignore */ } return; }
        xterm = h;
        h.onData((d) => onInputRef.current(d));
        handleRef.current = h;
        register(h);
        let timer: ReturnType<typeof setTimeout> | null = null;
        const ro = new ResizeObserver(() => {
          if (timer) clearTimeout(timer);
          timer = setTimeout(() => { try { h.fit(); } catch { /* ignore */ } if (h.cols() > 0) onResizeRef.current(h.cols(), h.rows()); }, 120);
        });
        try { ro.observe(host); } catch { /* ignore */ }
        (h as unknown as { __ro?: ResizeObserver }).__ro = ro;
      }).catch(() => undefined);
    }
    const fallback: TermHandle = {
      write: (data) => {
        if (xterm) { try { (xterm as unknown as TermHandle).write?.(data); } catch { /* ignore */ } return; }
        const el = linesRef.current;
        if (!el) return;
        const text = typeof data === 'string' ? data : new TextDecoder().decode(data);
        el.textContent = (el.textContent + text).slice(-200000);
        el.scrollTop = el.scrollHeight;
      },
      clear: () => { if (linesRef.current) linesRef.current.textContent = ''; },
      focus: () => { inputRef.current?.focus(); },
      cols: () => 80,
      rows: () => 24,
      paste: (t) => { if (t.includes('\n')) { if (!window.confirm('Paste multiple lines into the terminal?')) return; } onInputRef.current(t); },
    };
    handleRef.current = fallback;
    register(fallback);
    void lines;
    return () => { cancelled = true; register(null); };
  }, [collapsed, register]);

  if (collapsed) {
    return (
      <section className="ide-terminal is-collapsed" aria-label="Terminal">
        <div className="ide-terminal-header">
          <span>TERMINAL \u00b7 {label}</span>
          <button type="button" aria-label="Expand terminal" title="Expand terminal" onClick={onToggleCollapse}><Maximize2 size={14} /></button>
        </div>
      </section>
    );
  }
  return (
    <section className={`ide-terminal${maximized ? ' is-max' : ''}`} style={height ? { height } : undefined} aria-label="Terminal">
      <div className="ide-terminal-header">
        <span role="status" title={`Terminal ${label}`}>TERMINAL \u00b7 {label}</span>
        <span className="ide-terminal-actions">
          <button type="button" aria-label="New terminal" title="Restart shell (single PTY)" onClick={onOpen}><Plus size={14} /></button>
          <button type="button" aria-label="Close terminal process" title="Close shell (keeps files connected)" onClick={onClosePty}><Square size={14} /></button>
          <button type="button" aria-label="Clear terminal display" title="Clear display" onClick={onClear}><Trash2 size={14} /></button>
          <button type="button" aria-label={maximized ? 'Restore terminal' : 'Maximize terminal'} title={maximized ? 'Restore' : 'Maximize'} onClick={onToggleMax}>{maximized ? <Minimize2 size={14} /> : <Maximize2 size={14} />}</button>
          <button type="button" aria-label="Collapse terminal" title="Collapse terminal" onClick={onToggleCollapse}><X size={14} /></button>
        </span>
      </div>
      <div className="ide-terminal-body" ref={hostRef}>
        <div className="ide-terminal-lines" ref={linesRef} aria-hidden="true" />
        <input ref={inputRef} className="ide-terminal-input" aria-label="Terminal input" placeholder={pty === 'open' ? 'Type here and press Enter…' : 'Start the terminal to type…'} disabled={pty !== 'open'}
          onKeyDown={(e) => { if (e.key === 'Enter') { const el = e.currentTarget; const v = el.value; el.value = ''; if (v) onInput(`${v}\n`); } }} />
      </div>
    </section>
  );
}
