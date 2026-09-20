// Narrow wrappers so component tests can fake editor/terminal hosts without
// replacing transport behaviour. Real imports stay local (no CDN).
//
// EditorModel abstracts one open file's text model. The production adapter
// (`monacoAdapter.ts`) backs it with a real Monaco model; unit/component tests
// use the in-memory implementation in `hooks/useEditorModels.ts`.
export interface EditorModel {
  getValue(): string;
  setValue(v: string): void;
  dispose(): void;
  subscribe(cb: () => void): () => void;
}
export function uriFor(runtimeId: string, path: string): string {
  return `dml-workspace://${runtimeId}/${path.split('/').map(encodeURIComponent).join('/')}`;
}
// Terminal surface consumed by WorkspaceIDE. `xtermAdapter.ts` provides the
// real Xterm.js implementation; jsdom tests fall back to the DOM-lines
// surface automatically (see TerminalPanel).
export interface TermHandle {
  write(data: Uint8Array | string): void;
  clear(): void;
  focus(): void;
  cols(): number;
  rows(): number;
  paste(text: string): void;
}
