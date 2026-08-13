import React, { useMemo, useState } from 'react';
import { Search, Server, Terminal, Unplug } from 'lucide-react';
import {
  nodes as seedNodes,
  type ClusterNode,
  type NodeStatus,
  type NodeSortKey,
} from '../data/mock';

type StatusFilter = 'all' | NodeStatus;

const STATUS_LABEL: Record<NodeStatus, string> = {
  online: 'Online',
  offline: 'Offline',
  draining: 'Draining',
};

const getStatusBadge = (status: NodeStatus) => (
  <span className={`badge badge-${status}`}>{STATUS_LABEL[status]}</span>
);

const Nodes: React.FC = () => {
  const [nodeList, setNodeList] = useState<ClusterNode[]>(seedNodes);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [regionFilter, setRegionFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [sortKey, setSortKey] = useState<NodeSortKey>('name');
  const [actionFeedback, setActionFeedback] = useState<string | null>(null);

  const regions = useMemo(
    () => Array.from(new Set(nodeList.map((n) => n.region))).sort(),
    [nodeList],
  );

  const visibleNodes = useMemo(() => {
    const filtered = nodeList.filter((n) => {
      if (statusFilter !== 'all' && n.status !== statusFilter) return false;
      if (regionFilter !== 'all' && n.region !== regionFilter) return false;
      if (search.trim()) {
        const q = search.toLowerCase();
        if (!`${n.name} ${n.ip} ${n.gpuModel}`.toLowerCase().includes(q)) return false;
      }
      return true;
    });

    return [...filtered].sort((a, b) => {
      switch (sortKey) {
        case 'load': return b.load - a.load;
        case 'mem': return b.mem - a.mem;
        case 'gpus': return b.gpuCount - a.gpuCount;
        case 'vram': return b.vramPerGpu - a.vramPerGpu;
        case 'running': return b.runningJobs - a.runningJobs;
        case 'uptime': return b.uptimeHours - a.uptimeHours;
        case 'name':
        default: return a.name.localeCompare(b.name);
      }
    });
  }, [nodeList, statusFilter, regionFilter, search, sortKey]);

  const showFeedback = (msg: string) => {
    setActionFeedback(msg);
    window.setTimeout(() => setActionFeedback(null), 3000);
  };

  const handleDisconnect = (node: ClusterNode) => {
    setNodeList((prev) =>
      prev.map((n) =>
        n.id === node.id
          ? { ...n, status: n.status === 'online' ? 'offline' : 'online', load: 0, mem: 0, runningJobs: 0 }
          : n,
      ),
    );
    showFeedback(`${node.name} disconnected`);
  };

  const handleSsh = (node: ClusterNode) => {
    showFeedback(`Opening SSH session to ${node.name} (${node.ip}:${node.sshPort})...`);
  };

  return (
    <div className="fade-in">
      <h1>Cluster Nodes</h1>

      <div className="toolbar">
        <div className="toolbar-controls">
          <div className="toolbar-group">
            <label className="form-label">Search</label>
            <div style={{ position: 'relative' }}>
              <Search size={16} style={{ position: 'absolute', left: '0.75rem', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-secondary)' }} />
              <input
                className="form-input"
                style={{ width: 220, paddingLeft: '2.25rem', paddingTop: '0.5rem', paddingBottom: '0.5rem' }}
                placeholder="name, IP, GPU..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
          </div>
          <div className="toolbar-group">
            <label className="form-label">Status</label>
            <select
              className="form-select"
              style={{ width: 'auto', padding: '0.5rem 1rem' }}
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
            >
              <option value="all">All Statuses</option>
              <option value="online">Online</option>
              <option value="offline">Offline</option>
              <option value="draining">Draining</option>
            </select>
          </div>
          <div className="toolbar-group">
            <label className="form-label">Region</label>
            <select
              className="form-select"
              style={{ width: 'auto', padding: '0.5rem 1rem' }}
              value={regionFilter}
              onChange={(e) => setRegionFilter(e.target.value)}
            >
              <option value="all">All Regions</option>
              {regions.map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
          </div>
          <div className="toolbar-group">
            <label className="form-label">Sort By</label>
            <select
              className="form-select"
              style={{ width: 'auto', padding: '0.5rem 1rem' }}
              value={sortKey}
              onChange={(e) => setSortKey(e.target.value as NodeSortKey)}
            >
              <option value="name">Name</option>
              <option value="load">Load</option>
              <option value="mem">Memory</option>
              <option value="gpus">GPU Count</option>
              <option value="vram">VRAM / GPU</option>
              <option value="running">Running Jobs</option>
              <option value="uptime">Uptime</option>
            </select>
          </div>
        </div>

        {actionFeedback && (
          <div
            style={{
              fontSize: '0.875rem',
              color: 'var(--accent-primary)',
              backgroundColor: 'rgba(59, 130, 246, 0.1)',
              padding: '0.5rem 1rem',
              borderRadius: 6,
            }}
          >
            {actionFeedback}
          </div>
        )}
      </div>

      <div className="table-container">
        <table>
          <thead>
            <tr>
              <th>Node</th>
              <th>Region</th>
              <th>Status</th>
              <th>GPU</th>
              <th>VRAM</th>
              <th>Load</th>
              <th>Mem</th>
              <th>Jobs</th>
              <th>Uptime</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {visibleNodes.length === 0 && (
              <tr>
                <td colSpan={10} style={{ textAlign: 'center', color: 'var(--text-secondary)', padding: '2rem' }}>
                  No nodes match the current filters.
                </td>
              </tr>
            )}
            {visibleNodes.map((node) => (
              <tr key={node.id}>
                <td>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem' }}>
                    <Server size={16} color={node.status === 'online' ? 'var(--status-online)' : 'var(--status-offline)'} />
                    <div>
                      <div style={{ fontWeight: 600 }}>{node.name}</div>
                      <div className="mono" style={{ color: 'var(--text-secondary)' }}>
                        {node.ip}
                      </div>
                    </div>
                  </div>
                </td>
                <td>{node.region}</td>
                <td>{getStatusBadge(node.status)}</td>
                <td style={{ fontSize: '0.8125rem' }}>{node.gpuModel} ×{node.gpuCount}</td>
                <td>{node.vramPerGpu} GB</td>
                <td style={{ minWidth: 110 }}>
                  <div className="progress-bar-track">
                    <div className="progress-bar-fill load" style={{ width: `${node.load}%` }} />
                  </div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginTop: '0.25rem' }}>
                    {node.load}%
                  </div>
                </td>
                <td style={{ minWidth: 110 }}>
                  <div className="progress-bar-track">
                    <div className="progress-bar-fill mem" style={{ width: `${node.mem}%` }} />
                  </div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginTop: '0.25rem' }}>
                    {node.mem}%
                  </div>
                </td>
                <td>{node.runningJobs}</td>
                <td>{node.uptimeHours > 0 ? `${(node.uptimeHours / 24).toFixed(1)}d` : '—'}</td>
                <td>
                  <div style={{ display: 'flex', gap: '0.5rem' }}>
                    <button
                      className="btn btn-secondary btn-sm"
                      onClick={() => handleSsh(node)}
                    >
                      <Terminal size={14} />
                      SSH
                    </button>
                    <button
                      className="btn btn-danger btn-sm"
                      onClick={() => handleDisconnect(node)}
                      disabled={node.status === 'offline'}
                    >
                      <Unplug size={14} />
                      Disconnect
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default Nodes;
