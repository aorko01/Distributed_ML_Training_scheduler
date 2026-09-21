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
// the real Monaco editor bound to the per-path model (local bundle, no CDN,
// Graphite theme); jsdom/component tests keep the controlled textarea bound
// to the same per-path model registry, so transport behaviour stays under test.
export function EditorPane({ active, text, readOnly, fontSize, wordWrap, minimap, runtimeId, model, onChangeText, onToggleWrap, onToggleMinimap }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [monacoOk, setMonacoOk] = useState(false);
  const editorRef = useRef<{ dispose(): void; getValue(): string; setValue(v: string): void; updateOptions(o: Record<string, unknown>): void } | null>(null);
  const onChangeRef = useRef(onChangeText);
  onChangeRef.current = onChangeText;
  // The opaque EditorModel wrapper already carries per-path text; the Monaco
  // instance below mirrors it so typing, open/save/conflict stay in sync.
  const modelRef = useRef(model);
  modelRef.current = model;
  void runtimeId;

  useEffect(() => {
    const host = hostRef.current;
    if (!host || !active || typeof window === 'undefined' || navigator.userAgent.includes('jsdom')) return;
    let cancelled = false;
    let editor: { dispose(): void; getValue(): string; setValue(v: string): void; updateOptions(o: Record<string, unknown>): void; onDidChangeModelContent(cb: () => void): { dispose(): void }; getPosition(): { lineNumber: number; column: number } | null } | null = null;
    let contentSub: { dispose(): void } | null = null;
    let modelSub: (() => void) | null = null;
    void import('monaco-editor/esm/vs/editor/editor.api.js').then((m) => {
      if (cancelled || !host.isConnected) return;
      try {
        try {
          m.editor.defineTheme('dml-graphite', {
            base: 'vs-dark',
            inherit: true,
            rules: [],
            colors: {
              'editor.background': '#11151d',
              'editor.foreground': '#f1f5fb',
              'editor.lineHighlightBackground': '#1c232f',
              'editorLineNumber.foreground': '#627187',
              'editorLineNumber.activeForeground': '#b8dfff',
              'editorCursor.foreground': '#39a9ff',
              'editor.selectionBackground': '#39a9ff55',
              'editor.inactiveSelectionBackground': '#39a9ff33',
              'editorWidget.background': '#141a24',
              'editorWidget.border': '#2a3445',
              'editorGutter.background': '#11151d',
              'minimap.background': '#11151d',
            },
          });
        } catch { /* theme already defined */ }
        const initial = modelRef.current?.getValue() ?? text;
        const inst = m.editor.create(host, {
          theme: 'dml-graphite',
          automaticLayout: true,
          minimap: { enabled: minimap },
          wordWrap: wordWrap ? 'on' : 'off',
          fontSize,
          readOnly,
          value: initial,
          fontFamily: "'JetBrains Mono', Menlo, Consolas, monospace",
          scrollBeyondLastLine: false,
          padding: { top: 8 },
        });
        if (cancelled) { try { inst.dispose(); } catch { /* ignore */ } return; }
        editor = inst;
        editorRef.current = inst as unknown as typeof editorRef.current;
        setMonacoOk(true);
        contentSub = inst.onDidChangeModelContent(() => {
          const v = inst.getValue();
          // Mirror into the shared per-path model so save/conflict see edits.
          try { modelRef.current?.setValue(v); } catch { /* wrapper syncs separately */ }
          const pos = inst.getPosition();
          onChangeRef.current(v, pos?.lineNumber ?? 1, pos?.column ?? 1);
        });
        // External updates (open/save/conflict reload) flow back into Monaco.
        modelSub = modelRef.current?.subscribe(() => {
          const latest = modelRef.current?.getValue();
          if (latest !== undefined && inst.getValue() !== latest) {
            try { inst.setValue(latest); } catch { /* ignore */ }
          }
        }) ?? null;
      } catch { /* fall back to textarea */ }
    }).catch(() => undefined);
    return () => {
      cancelled = true;
      try { contentSub?.dispose(); } catch { /* ignore */ }
      try { modelSub?.(); } catch { /* ignore */ }
      try { editor?.dispose(); } catch { /* ignore */ }
      editorRef.current = null;
      setMonacoOk(false);
    };
    // Re-create per file; live option/text sync happens below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  // Keep options in sync without re-creating the editor (and its undo stack).
  useEffect(() => {
    try { editorRef.current?.updateOptions({ readOnly, fontSize, wordWrap: wordWrap ? 'on' : 'off', minimap: { enabled: minimap } }); } catch { /* textarea fallback */ }
  }, [readOnly, fontSize, wordWrap, minimap]);

  // Remote text changes (file open, save reload, conflict resolve) update
  // Monaco when the user is not actively driving it through onChange.
  useEffect(() => {
    if (!monacoOk) return;
    try {
      const cur = editorRef.current?.getValue();
      if (cur !== undefined && cur !== text) editorRef.current?.setValue(text);
    } catch { /* ignore */ }
  }, [text, monacoOk]);

  if (active === null) {
    return (
      <div className="ide-editor" role="tabpanel" aria-label="Editor">
        <div className="ide-editor-empty"><p>No file open.</p><p>Expand folders in the explorer and choose a UTF-8 text file.</p></div>
      </div>
    );
  }
  return (
    <div className="ide-editor" role="tabpanel" aria-label={active}>
      <div className="ide-editor-toolbar" role="toolbar" aria-label="Editor options">
        <button type="button" aria-pressed={wordWrap} title="Toggle word wrap" onClick={onToggleWrap}>Wrap</button>
        <button type="button" aria-pressed={minimap} title="Toggle minimap" onClick={onToggleMinimap}>Map</button>
        {monacoOk && <span className="ide-badge" title="Monaco editor active">monaco</span>}
      </div>
      <div className="ide-monaco-host" ref={hostRef} style={monacoOk ? undefined : { display: 'none' }} aria-hidden={!monacoOk} />
      {!monacoOk && (
        <textarea className="ide-textarea" aria-label={active} value={text} readOnly={readOnly} spellCheck={false} wrap={wordWrap ? 'soft' : 'off'} style={{ fontSize }} data-testid="ide-textarea"
          onChange={(e) => { const pos = e.target.selectionStart ?? e.target.value.length; const before = e.target.value.slice(0, pos); const line = before.split('\n').length; const col = pos - (before.lastIndexOf('\n') + 1) + 1; onChangeText(e.target.value, line, col); }} />
      )}
    </div>
  );
}
