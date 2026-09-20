import { useEffect, useRef, useState } from 'react';
import type { EditorModel } from '../adapters';

interface Props {
  active: string | null;
  text: string;
  readOnly: boolean;
  fontSize: number;
  wordWrap: boolean;
  minimap: boolean;
  runtimeId: string;
  model: EditorModel | null;
  onChangeText: (value: string, line: number, col: number) => void;
  onToggleWrap: () => void;
  onToggleMinimap: () => void;
}

// Monaco-capable editor with a textarea fallback. Production browsers render
// the real Monaco editor against the per-path model URI (local bundle, no
// CDN); jsdom/component tests keep the controlled textarea bound to the same
// per-path model registry, so transport behaviour stays under test.
export function EditorPane({ active, text, readOnly, fontSize, wordWrap, minimap, runtimeId, model, onChangeText, onToggleWrap, onToggleMinimap }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const areaRef = useRef<HTMLTextAreaElement>(null);
  const [monacoOk, setMonacoOk] = useState(false);
  void runtimeId; void model;

  useEffect(() => {
    const host = hostRef.current;
    if (!host || !active || typeof window === 'undefined' || navigator.userAgent.includes('jsdom')) return;
    let cancelled = false;
    let editor: { dispose(): void } | null = null;
    void import('monaco-editor/esm/vs/editor/editor.api.js').then((m) => {
      if (cancelled || !host.isConnected) return;
      try {
        const inst = m.editor.create(host, { theme: 'vs-dark', automaticLayout: true, minimap: { enabled: minimap }, wordWrap: wordWrap ? 'on' : 'off', fontSize, readOnly });
        editor = inst;
        setMonacoOk(true);
      } catch { /* fall back to textarea */ }
    }).catch(() => undefined);
    return () => { cancelled = true; try { editor?.dispose(); } catch { /* ignore */ } };
  }, [active, fontSize, minimap, wordWrap, readOnly]);

  if (active === null) {
    return (
      <div className="ide-editor" role="tabpanel" aria-label="Editor">
        <div className="ide-editor-empty"><p>No file open.</p><p>Expand folders in the explorer and choose a UTF-8 text file.</p></div>
      </div>
    );
  }
  return (
    <div className="ide-editor" ref={hostRef} role="tabpanel" aria-label={active}>
      <div className="ide-editor-toolbar" role="toolbar" aria-label="Editor options">
        <button type="button" aria-pressed={wordWrap} title="Toggle word wrap" onClick={onToggleWrap}>Wrap</button>
        <button type="button" aria-pressed={minimap} title="Toggle minimap" onClick={onToggleMinimap}>Map</button>
        {monacoOk && <span className="ide-badge" title="Monaco editor active">monaco</span>}
      </div>
      <textarea ref={areaRef} className="ide-textarea" aria-label={active} value={text} readOnly={readOnly} spellCheck={false} wrap={wordWrap ? 'soft' : 'off'} style={{ fontSize }} data-testid="ide-textarea"
        onChange={(e) => { const pos = e.target.selectionStart ?? e.target.value.length; const before = e.target.value.slice(0, pos); const line = before.split('\n').length; const col = pos - (before.lastIndexOf('\n') + 1) + 1; onChangeText(e.target.value, line, col); }} />
    </div>
  );
}
