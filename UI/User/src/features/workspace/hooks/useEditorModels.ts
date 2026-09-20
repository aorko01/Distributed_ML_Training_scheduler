import { useCallback, useEffect, useRef } from 'react';
import type { EditorModel } from '../adapters';

export function createMemoryModel(text: string): EditorModel {
  let value = text;
  const subs = new Set<() => void>();
  return {
    getValue: () => value,
    setValue: (v: string) => { if (v !== value) { value = v; for (const fn of [...subs]) fn(); } },
    dispose: () => { subs.clear(); },
    subscribe: (cb: () => void) => { subs.add(cb); return () => { subs.delete(cb); }; },
  };
}

type Factory = (path: string, initialText: string) => EditorModel;

/**
 * Per-path model registry. Production passes a Monaco-backed factory
 * (`acquireMonacoModel`); tests use the in-memory default. Exactly one model
 * per open path; the registry never calls `editor.setValue()` on a shared
 * anonymous model, preserving undo history and per-tab dirty tracking.
 */
export function useEditorModels(factory?: Factory) {
  const models = useRef(new Map<string, EditorModel>());
  const texts = useRef<Record<string, string>>({});
  const listeners = useRef(new Map<string, () => void>());
  const notifyRef = useRef<(() => void) | null>(null);
  const factoryRef = useRef(factory);
  factoryRef.current = factory;

  useEffect(() => () => {
    for (const unsub of listeners.current.values()) { try { unsub(); } catch { /* ignore */ } }
    listeners.current.clear();
    for (const m of models.current.values()) { try { m.dispose(); } catch { /* ignore */ } }
    models.current.clear(); texts.current = {};
  }, []);

  const ensure = useCallback((path: string, initialText: string): EditorModel => {
    const existing = models.current.get(path);
    if (existing) return existing;
    const make = factoryRef.current ?? createMemoryModel;
    const model = make(path, initialText);
    models.current.set(path, model);
    texts.current[path] = initialText;
    const unsub = model.subscribe(() => { texts.current[path] = model.getValue(); notifyRef.current?.(); });
    listeners.current.set(path, unsub);
    return model;
  }, []);

  const get = useCallback((path: string): EditorModel | undefined => models.current.get(path), []);
  const getText = useCallback((path: string): string | undefined => {
    const m = models.current.get(path);
    return m ? m.getValue() : texts.current[path];
  }, []);
  const setText = useCallback((path: string, text: string) => {
    const m = models.current.get(path);
    if (m) m.setValue(text);
    else texts.current[path] = text;
  }, []);
  const remove = useCallback((path: string) => {
    const unsub = listeners.current.get(path);
    if (unsub) { try { unsub(); } catch { /* ignore */ } listeners.current.delete(path); }
    const m = models.current.get(path);
    if (m) { try { m.dispose(); } catch { /* ignore */ } models.current.delete(path); }
    delete texts.current[path];
  }, []);
  const rename = useCallback((oldPath: string, newPath: string) => {
    const m = models.current.get(oldPath);
    if (m) { models.current.delete(oldPath); models.current.set(newPath, m); }
    if (oldPath in texts.current) { texts.current[newPath] = texts.current[oldPath]; delete texts.current[oldPath]; }
    const unsub = listeners.current.get(oldPath);
    if (unsub) { listeners.current.delete(oldPath); listeners.current.set(newPath, unsub); }
  }, []);

  return { modelsRef: models, textsRef: texts, ensure, get, getText, setText, remove, rename, onChange: (cb: () => void) => { notifyRef.current = cb; } };
}
