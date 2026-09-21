import { useEffect, useRef, useState } from 'react';
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
  onHeightChange?: (height: number) => void;
  register: (h: TermHandle | null) => void;
}

// Xterm surface with a DOM-lines fallback. Production browsers lazily mount
// the real Xterm.js terminal (local bundle + bundled xterm.css, FitAddon,
// debounced resize); jsdom/component tests use the lightweight fallback
// through the same TermHandle interface so transport behaviour stays under
// test. Exactly one surface is visible at a time: the xterm host is hidden
// until the live terminal is ready, and the fallback lines/input are removed
// once it is, so PTY output can never land on an invisible surface.
export function TerminalPanel({ pty, exit, collapsed, maximized, height, onOpen, onClosePty, onClear, onToggleCollapse, onToggleMax, onInput, onResize, onHeightChange, register }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const linesRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const liveRef = useRef<{ focus(): void } | null>(null);
  // Last dims actually reported to the backend. Fit can fire repeatedly
  // (panel drags, fonts, observer loops) — resending identical PTY_RESIZE
  // frames on every fire storms the worker with exec_resize calls, and one
  // transient docker-API failure there used to cost the whole session.
  const lastDims = useRef<{ c: number; r: number } | null>(null);
  const [xtermReady, setXtermReady] = useState(false);
  const label = pty === 'open' ? 'running' : pty === 'opening' ? 'starting' : pty === 'closing' ? 'closing' : pty === 'exited' ? `exited (${exit?.code ?? 0})` : 'closed';
  const onInputRef = useRef(onInput);
  onInputRef.current = onInput;
  const onResizeRef = useRef(onResize);
  onResizeRef.current = onResize;
  const registerRef = useRef(register);
  registerRef.current = register;

  const focusSurface = () => {
    if (liveRef.current) { try { liveRef.current.focus(); } catch { /* ignore */ } return; }
    inputRef.current?.focus();
  };

  useEffect(() => {
    const host = hostRef.current;
    if (!host || collapsed) return;
    let cancelled = false;
    let xterm: { dispose(): void; fit(): void; onData(cb: (d: string) => void): void } | null = null;
    let ro: ResizeObserver | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let attempts = 0;
    const isJsdom = typeof navigator !== 'undefined' && navigator.userAgent.includes('jsdom');

    const fallback: TermHandle = {
      write: (data) => {
        const el = linesRef.current;
        if (xterm) { try { (xterm as unknown as TermHandle).write?.(data); } catch { /* ignore */ } }
        if (!el) return;
        const text = typeof data === 'string' ? data : new TextDecoder().decode(data);
        el.textContent = (el.textContent + text).slice(-200000);
        el.scrollTop = el.scrollHeight;
      },
      clear: () => { if (linesRef.current) linesRef.current.textContent = ''; try { (xterm as unknown as TermHandle | null)?.clear?.(); } catch { /* ignore */ } },
      focus: () => { inputRef.current?.focus(); },
      cols: () => 80,
      rows: () => 24,
      paste: (t) => { if (t.includes('\n')) { if (!window.confirm('Paste multiple lines into the terminal?')) return; } onInputRef.current(t); },
    };
    registerRef.current(fallback);

    if (!isJsdom) {
      const mount = () => {
        if (cancelled || !host.isConnected) return;
        // The host is hidden by CSS until xterm is ready; unhide it inline
        // so it is measurable. On failure the inline style is reset so the
        // fallback surface keeps the full panel height.
        host.style.display = 'block';
        const rect = host.getBoundingClientRect();
        // The panel animates open; retry a few frames until it is measurable
        // instead of giving up and leaving a dead fallback surface.
        if ((rect.width < 2 || rect.height < 2) && attempts < 20) {
          attempts += 1;
          requestAnimationFrame(mount);
          return;
        }
        if (rect.width < 2 || rect.height < 2) { host.style.display = ''; return; }
        void import('../xtermAdapter.js').then((m) => {
          if (cancelled || !host.isConnected) return;
          const h = m.createXterm(host);
          if (!h || cancelled) { try { h?.dispose(); } catch { /* ignore */ } host.style.display = ''; return; }
          xterm = h;
          liveRef.current = h;
          const live: TermHandle = {
            write: (data) => {
              try { h.write(data); } catch { /* ignore */ }
              const el = linesRef.current;
              if (el) { const text = typeof data === 'string' ? data : new TextDecoder().decode(data); el.textContent = (el.textContent + text).slice(-200000); }
            },
            clear: () => { try { h.clear(); } catch { /* ignore */ } if (linesRef.current) linesRef.current.textContent = ''; },
            focus: () => { try { h.focus(); } catch { inputRef.current?.focus(); } },
            cols: () => { try { return h.cols(); } catch { return 80; } },
            rows: () => { try { return h.rows(); } catch { return 24; } },
            paste: (t) => { try { h.paste(t); } catch { onInputRef.current(t); } },
          };
          registerRef.current(live);
          setXtermReady(true);
          h.onData((d) => onInputRef.current(d));
          const emitSize = () => {
            let c = 0, r = 0;
            try { c = h.cols(); r = h.rows(); } catch { return; }
            if (c <= 0 || r <= 0) return;
            const prev = lastDims.current;
            if (prev && prev.c === c && prev.r === r) return;
            lastDims.current = { c, r };
            onResizeRef.current(c, r);
          };
          try { h.fit(); } catch { /* ignore */ }
          emitSize();
          ro = new ResizeObserver(() => {
            if (timer) clearTimeout(timer);
            timer = setTimeout(() => { try { h.fit(); } catch { /* ignore */ } emitSize(); }, 120);
          });
          try { ro.observe(host); } catch { /* ignore */ }
        }).catch(() => { host.style.display = ''; });
      };
      requestAnimationFrame(mount);
    }
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      try { ro?.disconnect(); } catch { /* ignore */ }
      try { (xterm as unknown as { dispose?: () => void })?.dispose?.(); } catch { /* ignore */ }
      xterm = null;
      liveRef.current = null;
      lastDims.current = null;
      setXtermReady(false);
      registerRef.current(null);
    };
    // Stable mount: callbacks go through refs, register through a ref, so
    // re-renders (typing, tabs, toasts) must not tear down the PTY surface.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [collapsed]);

  // Vertical drag-to-resize like real editors (disabled when maximized).

  if (collapsed) {
    return (
      <section className="ide-terminal is-collapsed" aria-label="Terminal">
        <div className="ide-terminal-header">
          <span>TERMINAL · {label}</span>
          <button type="button" aria-label="Expand terminal" title="Expand terminal" onClick={onToggleCollapse}><Maximize2 size={14} /></button>
        </div>
      </section>
    );
  }
  return (
    <section className={`ide-terminal${maximized ? ' is-max' : ''}${xtermReady ? ' has-xterm' : ''}`} style={maximized ? undefined : (height ? { height } : undefined)} aria-label="Terminal">
      {!maximized && onHeightChange && (
        <div className="ide-terminal-resizer" role="separator" aria-orientation="horizontal" aria-label="Resize terminal height" title="Drag to resize terminal"
          tabIndex={0}
          onKeyDown={(e) => { if (!onHeightChange) return; if (e.key === 'ArrowUp') onHeightChange(Math.min(640, (height ?? 260) + 16)); if (e.key === 'ArrowDown') onHeightChange(Math.max(120, (height ?? 260) - 16)); }}
          onMouseDown={(e) => {
            const startY = e.clientY;
            const startH = height ?? 260;
            const move = (ev: MouseEvent) => onHeightChange(Math.min(640, Math.max(120, startH + (startY - ev.clientY))));
            const up = () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); };
            window.addEventListener('mousemove', move);
            window.addEventListener('mouseup', up);
          }} />
      )}
      <div className="ide-terminal-header">
        <span role="status" title={`Terminal ${label}`}>TERMINAL · {label}</span>
        <span className="ide-terminal-actions">
          <button type="button" aria-label="New terminal" title="Restart shell (single PTY)" onClick={onOpen}><Plus size={14} /></button>
          <button type="button" aria-label="Close terminal process" title="Close shell (keeps files connected)" onClick={onClosePty}><Square size={14} /></button>
          <button type="button" aria-label="Clear terminal display" title="Clear display" onClick={onClear}><Trash2 size={14} /></button>
          <button type="button" aria-label={maximized ? 'Restore terminal' : 'Maximize terminal'} title={maximized ? 'Restore' : 'Maximize'} onClick={onToggleMax}>{maximized ? <Minimize2 size={14} /> : <Maximize2 size={14} />}</button>
          <button type="button" aria-label="Collapse terminal" title="Collapse terminal" onClick={onToggleCollapse}><X size={14} /></button>
        </span>
      </div>
      <div className="ide-terminal-body" onClick={focusSurface}>
        <div className="ide-terminal-xterm" ref={hostRef} />
        <div className="ide-terminal-lines" ref={linesRef} aria-hidden={xtermReady} />
        {!xtermReady && (
          <input ref={inputRef} className="ide-terminal-input" aria-label="Terminal input" placeholder={pty === 'open' ? 'Type here and press Enter…' : 'Start the terminal to type…'} disabled={pty !== 'open'}
            onKeyDown={(e) => { if (e.key === 'Enter') { const el = e.currentTarget; const v = el.value; el.value = ''; if (v) onInput(`${v}\n`); } }} />
        )}
      </div>
    </section>
  );
}
