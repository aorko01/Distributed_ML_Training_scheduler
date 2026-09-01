import React, { useState } from 'react';
import {
  Folder,
  FolderOpen,
  FileText,
  FilePlus,
  Database,
  Image as ImageIcon,
  FileCode2,
} from 'lucide-react';

interface FileNode {
  name: string;
  children?: FileNode[];
  kind?: 'data' | 'image' | 'code';
  notebook?: boolean;
}

const fileIcon = (node: FileNode, size = 15) => {
  if (node.notebook) return <FileText size={size} color="var(--text)" />;
  switch (node.kind) {
    case 'data': return <Database size={size} color="var(--accent-primary)" />;
    case 'image': return <ImageIcon size={size} color="var(--status-building)" />;
    case 'code': return <FileCode2 size={size} color="var(--status-success)" />;
    default: return <FileText size={size} color="var(--text-secondary)" />;
  }
};

interface FileExplorerProps {
  files?: FileNode[];
  onOpenNotebook: (name: string) => void;
  onNewNotebook: () => void;
}

const FileExplorer: React.FC<FileExplorerProps> = ({ files = [], onOpenNotebook, onNewNotebook }) => {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<string>('');

  const toggle = (path: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const renderNodes = (nodes: FileNode[], parentPath: string, depth: number) =>
    nodes.map((node) => {
      const path = `${parentPath}/${node.name}`;
      const isFolder = node.children !== undefined;
      const isOpen = expanded.has(path);
      return (
        <React.Fragment key={path}>
          <button
            className={`nb-file-row ${selected === path ? 'selected' : ''}`}
            style={{ paddingLeft: `${0.75 + depth * 0.9}rem` }}
            onClick={() => {
              setSelected(path);
              if (isFolder) toggle(path);
              else if (node.notebook) onOpenNotebook(node.name);
            }}
          >
            {isFolder
              ? (isOpen
                ? <FolderOpen size={15} color="var(--accent-primary)" />
                : <Folder size={15} color="var(--text-secondary)" />)
              : fileIcon(node)}
            <span className="nb-file-name">{node.name}</span>
          </button>
          {isFolder && isOpen && renderNodes(node.children ?? [], path, depth + 1)}
        </React.Fragment>
      );
    });

  return (
    <aside className="nb-explorer">
      <div className="nb-explorer-head">
        <span className="nb-rail-label">Files</span>
        <div style={{ display: 'flex', gap: '0.15rem' }}>
          <button className="nb-mini-btn" title="New notebook" onClick={onNewNotebook}>
            <FilePlus size={14} />
          </button>
        </div>
      </div>
      <div className="nb-explorer-tree">
        {files.length === 0 ? (
          <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', padding: '0.75rem' }}>
            No files loaded.
          </p>
        ) : (
          renderNodes(files, '', 0)
        )}
      </div>
    </aside>
  );
};

export default FileExplorer;
