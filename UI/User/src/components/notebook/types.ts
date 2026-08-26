export type CellType = 'code' | 'markdown';
export type CellState = 'idle' | 'running' | 'done';

export interface OutputBlock {
  kind: 'text' | 'success' | 'error';
  text: string;
}

export interface NotebookCell {
  id: string;
  type: CellType;
  source: string;
  outputs: OutputBlock[];
  state: CellState;
  execCount: number | null;
}

export interface NotebookTab {
  id: string;
  name: string;
  cells: NotebookCell[];
}

let idCounter = 0;

export const makeId = (prefix: string): string => {
  idCounter += 1;
  return `${prefix}-${idCounter}-${Math.random().toString(36).slice(2, 7)}`;
};

export const makeCodeCell = (source = ''): NotebookCell => ({
  id: makeId('cell'),
  type: 'code',
  source,
  outputs: [],
  state: 'idle',
  execCount: null,
});

export const makeMarkdownCell = (source = ''): NotebookCell => ({
  id: makeId('cell'),
  type: 'markdown',
  source,
  outputs: [],
  state: 'idle',
  execCount: null,
});
