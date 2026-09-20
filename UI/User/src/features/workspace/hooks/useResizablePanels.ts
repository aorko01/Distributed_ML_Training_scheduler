import { useCallback, useEffect, useState } from 'react';
const NS = 'dml.workspace.v1.';
function read(key: string, fallback: number): number { try { const v = Number(localStorage.getItem(NS + key)); return Number.isFinite(v) && v > 0 ? v : fallback; } catch { return fallback; } }
function readBool(key: string, fallback: boolean): boolean { try { const v = localStorage.getItem(NS + key); return v === null ? fallback : v === '1'; } catch { return fallback; } }
function write(key: string, value: string): void { try { localStorage.setItem(NS + key, value); } catch { /* ignore */ } }
export function useResizablePanels() {
  const [explorerWidth, setExplorerWidth] = useState(() => read('explorerWidth', 260));
  const [terminalHeight, setTerminalHeight] = useState(() => read('terminalHeight', 260));
  const [terminalCollapsed, setTerminalCollapsed] = useState(() => readBool('terminalCollapsed', false));
  const [explorerOpen, setExplorerOpen] = useState(() => readBool('explorerOpen', true));
  const [wordWrap, setWordWrap] = useState(() => readBool('wordWrap', false));
  const [minimap, setMinimap] = useState(() => readBool('minimap', false));
  useEffect(() => write('explorerWidth', String(explorerWidth)), [explorerWidth]);
  useEffect(() => write('terminalHeight', String(terminalHeight)), [terminalHeight]);
  useEffect(() => write('terminalCollapsed', terminalCollapsed ? '1' : '0'), [terminalCollapsed]);
  useEffect(() => write('explorerOpen', explorerOpen ? '1' : '0'), [explorerOpen]);
  useEffect(() => write('wordWrap', wordWrap ? '1' : '0'), [wordWrap]);
  useEffect(() => write('minimap', minimap ? '1' : '0'), [minimap]);
  const toggleExplorer = useCallback(() => setExplorerOpen((v) => !v), []);
  const toggleTerminal = useCallback(() => setTerminalCollapsed((v) => !v), []);
  return { explorerWidth, setExplorerWidth, terminalHeight, setTerminalHeight, terminalCollapsed, setTerminalCollapsed, explorerOpen, setExplorerOpen, wordWrap, setWordWrap, minimap, setMinimap, toggleExplorer, toggleTerminal };
}
