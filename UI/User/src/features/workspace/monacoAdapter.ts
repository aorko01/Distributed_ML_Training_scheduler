// Production Monaco adapter: one real Monaco model per open file.
// Loaded lazily so jsdom/unit tests never pull the Monaco bundle; local
// dependency (no CDN), consistent with instruction §5/§10.
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api.js';
import { languageFor } from './workspaceTypes';
import { uriFor, type EditorModel } from './adapters';

const models = new Map<string, { model: monaco.editor.ITextModel; refs: number }>();

export function monacoUri(runtimeId: string, path: string): monaco.Uri {
  return monaco.Uri.parse(uriFor(runtimeId, path));
}

export function acquireMonacoModel(runtimeId: string, path: string, text: string): EditorModel {
  const key = `${runtimeId} ${path}`;
  const existing = models.get(key);
  if (existing) {
    existing.refs += 1;
    return wrap(key, existing.model);
  }
  const model = monaco.editor.createModel(text, languageFor(path), monacoUri(runtimeId, path));
  models.set(key, { model, refs: 1 });
  return wrap(key, model);
}

function wrap(key: string, model: monaco.editor.ITextModel): EditorModel {
  return {
    getValue: () => model.getValue(),
    setValue: (v: string) => {
      if (v !== model.getValue()) {
        // Preserve undo history for local typing: only push an edit stack
        // element when synchronising after open/save/conflict.
        model.pushEditOperations([], [{ range: model.getFullModelRange(), text: v }], () => null);
      }
    },
    dispose: () => {
      const entry = models.get(key);
      if (!entry) return;
      entry.refs -= 1;
      if (entry.refs <= 0) {
        models.delete(key);
        try { entry.model.dispose(); } catch { /* ignore */ }
      }
    },
    subscribe: (cb: () => void) => {
      const d = model.onDidChangeContent(() => cb());
      return () => d.dispose();
    },
  };
}

export function disposeAllMonacoModels(): void {
  for (const [, entry] of [...models]) {
    try { entry.model.dispose(); } catch { /* ignore */ }
  }
  models.clear();
}
