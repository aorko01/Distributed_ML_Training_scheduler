import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  ArrowDown,
  ArrowUp,
  BookOpen,
  Braces,
  Check,
  Copy,
  Eraser,
  FileText,
  Folder,
  Gauge,
  Loader2,
  Play,
  Plus,
  Power,
  RotateCcw,
  Share2,
  Terminal,
  Timer,
  Trash2,
  X,
  Zap,
} from 'lucide-react';
import FileExplorer from '../components/notebook/FileExplorer';
import FakeTerminal from '../components/notebook/FakeTerminal';
import SessionPanel from '../components/notebook/SessionPanel';
import {
  makeCodeCell,
  makeMarkdownCell,
  type NotebookCell,
  type NotebookTab,
  type OutputBlock,
} from '../components/notebook/types';

const PYTORCH_VERSION = '2.3.1';
const CUDA_VERSION = '12.1';
const ACCELERATOR = 'A100 80GB';

const WELCOME_CELLS = (): NotebookCell[] => [
  makeMarkdownCell(
    '# Interactive session ready!\n\nThis notebook runs on **node-a100-04** with a dedicated *NVIDIA A100 80GB* attached.\n\n- Persistent `/workspace` storage\n- Pre-installed PyTorch stack\n- Built-in terminal for installs and shell work',
  ),
  makeCodeCell(
    'import torch\n\nprint("GPU:", torch.cuda.get_device_name(0))\nprint("CUDA available:", torch.cuda.is_available())',
  ),
  makeMarkdownCell(
    '## Next steps\n\n1. Browse `data/` in the file explorer\n2. Open the terminal panel for `pip install`s\n3. Press **Run all** and watch the kernel go',
  ),
  makeCodeCell(''),
];

const simulateExecution = (source: string): OutputBlock[] => {
  const trimmed = source.trim();
  if (!trimmed) return [];

  if (trimmed.startsWith('!')) {
    const cmd = trimmed.slice(1).trim();
    if (cmd.startsWith('pip install')) {
      const pkg = cmd.replace('pip install', '').trim().split(/\s+/)[0] || 'package';
      return [
        { kind: 'text', text: `Collecting ${pkg}` },
        { kind: 'text', text: `  Downloading ${pkg}-1.2.3-cp311-cp311-manylinux_2_17_x86_64.whl (8.4 MB)` },
        { kind: 'text', text: `Installing collected packages: ${pkg}` },
        { kind: 'success', text: `Successfully installed ${pkg}-1.2.3` },
      ];
    }
    if (cmd === 'nvidia-smi') {
      return [{ kind: 'text', text: 'NVIDIA A100 80GB · 62C · 12672MiB / 81920MiB · 68% util' }];
    }
    return [{ kind: 'text', text: `[dummy] \`${cmd}\` executed on node-a100-04` }];
  }

  const lower = trimmed.toLowerCase();
  if (lower.includes('cuda.is_available')) {
    return [
      { kind: 'text', text: 'GPU: NVIDIA A100 80GB (cuda:0)' },
      { kind: 'text', text: 'CUDA available: True' },
      { kind: 'success', text: `PyTorch ${PYTORCH_VERSION} · CUDA ${CUDA_VERSION} · cuDNN 8.9.7` },
    ];
  }
  if (lower === 'import torch' || lower.includes('import torch')) {
    return [{ kind: 'text', text: `torch ${PYTORCH_VERSION}+cu${CUDA_VERSION.replace('.', '')} loaded` }];
  }

  const printMatch = trimmed.match(/print\(\s*(["'])([\s\S]*?)\1\s*\)/);
  if (printMatch) return [{ kind: 'text', text: printMatch[2] }];

  const arith = trimmed.match(/^(\d+(?:\.\d+)?)\s*([+\-*/])\s*(\d+(?:\.\d+)?)$/);
  if (arith) {
    const a = parseFloat(arith[1]);
    const b = parseFloat(arith[3]);
    const result =
      arith[2] === '+' ? a + b : arith[2] === '-' ? a - b : arith[2] === '*' ? a * b : b === 0 ? NaN : a / b;
    return [{ kind: 'text', text: String(result) }];
  }

  return [];
};

const formatClock = (seconds: number) => {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const mm = h > 0 ? m.toString().padStart(2, '0') : m.toString();
  return `${h > 0 ? `${h}:` : ''}${mm}:${s.toString().padStart(2, '0')}`;
};

const renderInline = (text: string): React.ReactNode[] =>
  text.split(/(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/g).map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.startsWith('`') && part.endsWith('`')) return <code key={i} className="nb-inline-code">{part.slice(1, -1)}</code>;
    if (part.length > 2 && part.startsWith('*') && part.endsWith('*')) return <em key={i}>{part.slice(1, -1)}</em>;
    return <React.Fragment key={i}>{part}</React.Fragment>;
  });

const MarkdownView: React.FC<{ source: string }> = ({ source }) => (
  <div className="nb-md">
    {source.split('\n').map((line, i) => {
      if (line.startsWith('### ')) return <h4 key={i}>{renderInline(line.slice(4))}</h4>;
      if (line.startsWith('## ')) return <h3 key={i}>{renderInline(line.slice(3))}</h3>;
      if (line.startsWith('# ')) return <h2 key={i}>{renderInline(line.slice(2))}</h2>;
      if (line.startsWith('- ')) return <li key={i}>{renderInline(line.slice(2))}</li>;
      if (line.startsWith('> ')) return <blockquote key={i}>{renderInline(line.slice(2))}</blockquote>;
      if (line.trim() === '') return <div key={i} style={{ height: '0.5rem' }} />;
      return <p key={i}>{renderInline(line)}</p>;
    })}
  </div>
);

interface CellViewProps {
  cell: NotebookCell;
  onChange: (source: string) => void;
  onRun: () => void;
  onDelete: () => void;
  onMove: (dir: -1 | 1) => void;
  onDuplicate: () => void;
  registerRef: (el: HTMLTextAreaElement | null) => void;
}

const CellView: React.FC<CellViewProps> = ({
  cell,
  onChange,
  onRun,
  onDelete,
  onMove,
  onDuplicate,
  registerRef,
}) => {
  const taRef = useRef<HTMLTextAreaElement>(null);
  const [editingMd, setEditingMd] = useState(false);

  useEffect(() => {
    const el = taRef.current;
    if (el) {
      el.style.height = 'auto';
      el.style.height = `${el.scrollHeight}px`;
    }
  }, [cell.source]);

  const gutterLabel =
    cell.type === 'code'
      ? cell.state === 'running'
        ? '[*]'
        : cell.execCount != null
          ? `[${cell.execCount}]`
          : '[ ]'
      : '';

  const busy = cell.state === 'running';

  return (
    <div className={`nb-cell ${busy ? 'nb-cell-busy' : ''}`}>
      {cell.type === 'code' && (
        <div className={`nb-gutter ${busy ? 'running' : ''}`} onClick={onRun}>
          <button className="nb-run-btn" title="Run cell">
            {busy ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
          </button>
          <span className="nb-exec-count">{gutterLabel}</span>
        </div>
      )}

      <div className="nb-cell-body">
        <div className="nb-cell-hover-bar">
          <button title="Move up" onClick={() => onMove(-1)}><ArrowUp size={13} /></button>
          <button title="Move down" onClick={() => onMove(1)}><ArrowDown size={13} /></button>
          <button title="Duplicate" onClick={onDuplicate}><Copy size={13} /></button>
          <button title="Delete cell" onClick={onDelete}><Trash2 size={13} /></button>
        </div>

        {cell.type === 'code' ? (
          <>
            <textarea
              ref={(el) => {
                taRef.current = el;
                registerRef(el);
              }}
              className="nb-code-area"
              value={cell.source}
              spellCheck={false}
              placeholder="# Write some Python…"
              onChange={(e) => onChange(e.target.value)}
              onKeyDown={(e) => {
                if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                  e.preventDefault();
                  onRun();
                }
              }}
            />
            {cell.outputs.length > 0 && (
              <div className="nb-output">
                {cell.outputs.map((block, i) => (
                  <pre
                    key={i}
                    className={
                      block.kind === 'error' ? 'term-error' : block.kind === 'success' ? 'term-success' : ''
                    }
                  >
                    {block.text}
                  </pre>
                ))}
              </div>
            )}
          </>
        ) : editingMd ? (
          <textarea
            ref={taRef}
            className="nb-md-area"
            value={cell.source}
            spellCheck={false}
            autoFocus
            placeholder="# Markdown…"
            onChange={(e) => onChange(e.target.value)}
            onBlur={() => setEditingMd(false)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setEditingMd(false);
              if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                e.preventDefault();
                setEditingMd(false);
              }
            }}
          />
        ) : (
          <div className="nb-md-wrap" onClick={() => setEditingMd(true)} title="Click to edit">
            {cell.source.trim() ? <MarkdownView source={cell.source} /> : <span className="nb-md-empty">Empty markdown — click to edit</span>}
          </div>
        )}
      </div>
    </div>
  );
};

const NotebookWorkspace: React.FC = () => {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();

  const initialTabs = useMemo<NotebookTab[]>(
    () => [{ id: 'welcome', name: 'Welcome.ipynb', cells: WELCOME_CELLS() }],
    [],
  );

  const [tabs, setTabs] = useState<NotebookTab[]>(initialTabs);
  const [activeTabId, setActiveTabId] = useState<string>('welcome');
  const [showExplorer, setShowExplorer] = useState(true);
  const [showPanel, setShowPanel] = useState(true);
  const [showTerminal, setShowTerminal] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<'saved' | 'saving'>('saved');
  const [uptime, setUptime] = useState(1543);

  const execCounter = useRef(0);
  const cellRefs = useRef<Record<string, HTMLTextAreaElement | null>>({});
  const toastTimer = useRef<number | undefined>(undefined);

  const activeTab = tabs.find((t) => t.id === activeTabId) ?? null;
  const busy = useMemo(
    () => tabs.some((t) => t.cells.some((c) => c.state === 'running')),
    [tabs],
  );

  const showToast = useCallback((msg: string) => {
    setToast(msg);
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 2400);
  }, []);

  useEffect(() => {
    const id = setInterval(() => setUptime((u) => u + 1), 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    setSaveStatus('saving');
    const id = window.setTimeout(() => setSaveStatus('saved'), 800);
    return () => window.clearTimeout(id);
  }, [tabs]);

  const mutateActiveCells = useCallback(
    (fn: (cells: NotebookCell[]) => NotebookCell[]) => {
      setTabs((prev) =>
        prev.map((t) => (t.id === activeTabId ? { ...t, cells: fn(t.cells) } : t)),
      );
    },
    [activeTabId],
  );

  const patchCell = useCallback(
    (cellId: string, patch: Partial<NotebookCell>) => {
      mutateActiveCells((cells) => cells.map((c) => (c.id === cellId ? { ...c, ...patch } : c)));
    },
    [mutateActiveCells],
  );

  const runCell = useCallback(
    (tabId: string, cellId: string) => {
      const startCount = ++execCounter.current;
      setTabs((prev) =>
        prev.map((t) =>
          t.id === tabId
            ? { ...t, cells: t.cells.map((c) => (c.id === cellId ? { ...c, state: 'running', outputs: [] } : c)) }
            : t,
        ),
      );
      const delay = 450 + Math.random() * 900;
      window.setTimeout(() => {
        setTabs((prev) =>
          prev.map((t) =>
            t.id === tabId
              ? {
                  ...t,
                  cells: t.cells.map((c) =>
                    c.id === cellId
                      ? { ...c, state: 'done', execCount: startCount, outputs: simulateExecution(c.source) }
                      : c,
                  ),
                }
              : t,
          ),
        );
      }, delay);
    },
    [],
  );

  const runActiveCell = useCallback(
    (cellId: string) => runCell(activeTabId, cellId),
    [runCell, activeTabId],
  );

  const runAll = useCallback(() => {
    const tab = tabs.find((t) => t.id === activeTabId);
    if (!tab) return;
    let offset = 0;
    tab.cells.forEach((c) => {
      if (c.type === 'code') {
        window.setTimeout(() => runCell(activeTabId, c.id), offset);
        offset += 1300;
      }
    });
  }, [tabs, activeTabId, runCell]);

  const focusNextCell = (cellId: string) => {
    const idx = activeTab?.cells.findIndex((c) => c.id === cellId) ?? -1;
    const next = activeTab?.cells[idx + 1];
    if (next) cellRefs.current[next.id]?.focus();
  };

  const addCell = (type: NotebookCell['type']) => {
    const cell = type === 'code' ? makeCodeCell() : makeMarkdownCell();
    mutateActiveCells((cells) => [...cells, cell]);
  };

  const deleteCell = (cellId: string) => {
    mutateActiveCells((cells) => cells.filter((c) => c.id !== cellId));
    delete cellRefs.current[cellId];
  };

  const moveCell = (cellId: string, dir: -1 | 1) => {
    mutateActiveCells((cells) => {
      const idx = cells.findIndex((c) => c.id === cellId);
      const target = idx + dir;
      if (idx < 0 || target < 0 || target >= cells.length) return cells;
      const copy = [...cells];
      [copy[idx], copy[target]] = [copy[target], copy[idx]];
      return copy;
    });
  };

  const duplicateCell = (cellId: string) => {
    mutateActiveCells((cells) => {
      const idx = cells.findIndex((c) => c.id === cellId);
      if (idx < 0) return cells;
      const src = cells[idx];
      const clone: NotebookCell = {
        ...(typeClone(src)),
        id: `${src.type}-copy-${Math.random().toString(36).slice(2, 8)}`,
      };
      return [...cells.slice(0, idx + 1), clone, ...cells.slice(idx + 1)];
    });
  };

  const typeClone = (src: NotebookCell): NotebookCell => ({
    ...src,
    outputs: src.outputs.map((o) => ({ ...o })),
  });

  const restartKernel = () => {
    setTabs((prev) =>
      prev.map((t) => ({
        ...t,
        cells: t.cells.map((c) =>
          c.type === 'code' ? { ...c, state: 'idle', outputs: [], execCount: null } : c,
        ),
      })),
    );
    showToast('Kernel restarted');
  };

  const clearOutputs = () => {
    setTabs((prev) =>
      prev.map((t) => ({
        ...t,
        cells: t.cells.map((c) => ({ ...c, outputs: [] })),
      })),
    );
    showToast('Outputs cleared');
  };

  const closeTab = (tabId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setTabs((prev) => {
      const next = prev.filter((t) => t.id !== tabId);
      if (tabId === activeTabId) setActiveTabId(next[next.length - 1]?.id ?? '');
      return next;
    });
  };

  const openNotebookByName = (name: string) => {
    const existing = tabs.find((t) => t.name === name);
    if (existing) {
      setActiveTabId(existing.id);
      return;
    }
    const tab: NotebookTab = { id: `nb-${Date.now()}`, name, cells: name === 'Welcome.ipynb' ? WELCOME_CELLS() : [makeCodeCell()] };
    setTabs((prev) => [...prev, tab]);
    setActiveTabId(tab.id);
  };

  const newNotebook = () => {
    const n = tabs.filter((t) => t.name.startsWith('Untitled')).length + 1;
    const tab: NotebookTab = { id: `nb-${Date.now()}`, name: `Untitled${n}.ipynb`, cells: [makeCodeCell()] };
    setTabs((prev) => [...prev, tab]);
    setActiveTabId(tab.id);
    showToast(`Created ${tab.name}`);
  };

  const renameActive = (name: string) => {
    setTabs((prev) => prev.map((t) => (t.id === activeTabId ? { ...t, name } : t)));
  };

  const share = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      showToast('Shareable link copied to clipboard');
    } catch {
      showToast(window.location.href);
    }
  };

  const stopSession = () => {
    if (window.confirm('Stop this interactive session? GPU will be released. (Dummy action)')) {
      navigate('/');
    }
  };

  return (
    <div className="nb-root fade-in">
      <header className="nb-header">
        <div className="nb-header-left">
          <button className="nb-icon-btn" onClick={() => navigate('/')} title="Back to dashboard">
            <ArrowLeft size={17} />
          </button>
          <FileText size={16} color="#f59e0b" />
          <input
            className="nb-title-input"
            value={activeTab?.name ?? ''}
            placeholder="Untitled notebook"
            onChange={(e) => renameActive(e.target.value)}
            spellCheck={false}
          />
          {saveStatus === 'saving' ? (
            <span className="nb-save-status"><Loader2 size={13} className="animate-spin" /> Saving…</span>
          ) : (
            <span className="nb-save-status saved"><Check size={13} /> Saved</span>
          )}
        </div>

        <div className="nb-header-right">
          <span className="nb-pill" title="Environment">
            <Braces size={13} /> PyTorch {PYTORCH_VERSION} · CUDA {CUDA_VERSION}
          </span>
          <span className="nb-pill nb-pill-accel" title="Accelerator">
            <Zap size={13} color="var(--status-pending)" /> {ACCELERATOR}
          </span>
          <span className="nb-pill" title="Session uptime">
            <Timer size={13} /> {formatClock(uptime)}
          </span>
          <button className="btn btn-secondary nb-header-btn" onClick={share}>
            <Share2 size={15} /> Share
          </button>
          <button className="btn nb-stop-btn" onClick={stopSession}>
            <Power size={15} /> Stop
          </button>
        </div>
      </header>

      <div className="nb-body">
        <nav className="nb-rail">
          <button
            className={`nb-rail-btn ${showExplorer ? 'active' : ''}`}
            title="Toggle file explorer"
            onClick={() => setShowExplorer((v) => !v)}
          >
            <Folder size={19} />
          </button>
          <button
            className={`nb-rail-btn ${showTerminal ? 'active' : ''}`}
            title="Toggle terminal"
            onClick={() => setShowTerminal((v) => !v)}
          >
            <Terminal size={19} />
          </button>
          <button
            className={`nb-rail-btn ${showPanel ? 'active' : ''}`}
            title="Toggle session panel"
            onClick={() => setShowPanel((v) => !v)}
          >
            <Gauge size={19} />
          </button>
        </nav>

        {showExplorer && (
          <FileExplorer onOpenNotebook={openNotebookByName} onNewNotebook={newNotebook} />
        )}

        <section className="nb-main">
          {tabs.length === 0 ? (
            <div className="nb-empty">
              <BookOpen size={40} color="var(--text-secondary)" />
              <h3>No notebook open</h3>
              <p>Create one to start hacking on the GPU.</p>
              <button className="btn btn-primary" onClick={newNotebook}>
                <Plus size={16} /> New notebook
              </button>
            </div>
          ) : (
            <>
              <div className="nb-tabbar">
                {tabs.map((tab) => (
                  <div
                    key={tab.id}
                    className={`nb-tab ${tab.id === activeTabId ? 'active' : ''}`}
                    onClick={() => setActiveTabId(tab.id)}
                  >
                    <FileText size={14} color="#f59e0b" />
                    <span>{tab.name}</span>
                    <button className="nb-tab-close" onClick={(e) => closeTab(tab.id, e)}>
                      <X size={12} />
                    </button>
                  </div>
                ))}
                <button className="nb-mini-btn" title="New notebook" onClick={newNotebook} style={{ marginLeft: '0.35rem' }}>
                  <Plus size={14} />
                </button>
              </div>

              <div className="nb-toolbar">
                <button className="nb-tool-btn" onClick={() => addCell('code')}><Plus size={14} /> Code</button>
                <button className="nb-tool-btn" onClick={() => addCell('markdown')}><Plus size={14} /> Markdown</button>
                <span className="nb-toolbar-sep" />
                <button className="nb-tool-btn" onClick={runAll}><Play size={14} /> Run all</button>
                <button className="nb-tool-btn" onClick={restartKernel}><RotateCcw size={14} /> Restart</button>
                <button className="nb-tool-btn" onClick={clearOutputs}><Eraser size={14} /> Clear outputs</button>
                <span style={{ flex: 1 }} />
                <span className={`nb-kernel-status ${busy ? 'busy' : ''}`}>
                  <span className="nb-kernel-dot" />
                  {busy ? 'Kernel busy' : 'Kernel ready'}
                </span>
              </div>

              <div className="nb-cells">
                {activeTab?.cells.map((cell) => (
                  <CellView
                    key={cell.id}
                    cell={cell}
                    onChange={(src) => patchCell(cell.id, { source: src })}
                    onRun={() => {
                      runActiveCell(cell.id);
                      focusNextCell(cell.id);
                    }}
                    onDelete={() => deleteCell(cell.id)}
                    onMove={(dir) => moveCell(cell.id, dir)}
                    onDuplicate={() => duplicateCell(cell.id)}
                    registerRef={(el) => {
                      cellRefs.current[cell.id] = el;
                    }}
                  />
                ))}
              </div>
            </>
          )}
        </section>

        {showPanel && (
          <SessionPanel busy={busy} uptimeSeconds={uptime} onOpenTerminal={() => setShowTerminal(true)} />
        )}
      </div>

      {showTerminal && <FakeTerminal onClose={() => setShowTerminal(false)} />}

      {toast && <div className="nb-toast">{toast}</div>}

      {sessionId && !activeTab && (
        <span className="visually-hidden">session {sessionId}</span>
      )}
    </div>
  );
};

export default NotebookWorkspace;
