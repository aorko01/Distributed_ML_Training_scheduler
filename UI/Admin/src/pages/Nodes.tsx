import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, Server, Plus, Trash2 } from 'lucide-react';
import {
  type ClusterNode,
  type NodeSortKey,
} from '../data/mock';
import {
  fetchNodes,
  fetchWorkerCredentials,
  registerWorkerCredential,
  revokeWorkerCredential,
  UnauthorizedError,
  type ApiNode,
  type WorkerCredential,
} from '../services/api';

type StatusFilter = 'all' | 'online' | 'offline';

const STATUS_LABEL: Record<'online' | 'offline', string> = {
  online: 'Online',
  offline: 'Offline',
};

const getStatusBadge = (status: string) => (
  <span className={`badge badge-${status}`}>
    {STATUS_LABEL[status as 'online' | 'offline'] ?? status}
  </span>
);

const roundMetric = (value: number | null | undefined, fallback = 0): number =>
  value == null ? fallback : Math.round(value);

const usingVramPercent = (node: ClusterNode): number => {
  if (!node.vramPerGpu) return 0;
  return Math.min(100, Math.round(((node.vramPerGpu - node.availableVram) / node.vramPerGpu) * 100));
};

const toClusterNode = (node: ApiNode): ClusterNode => ({
  id: node.worker_id,
  name: node.hostname || node.worker_id.slice(0, 8),
  // IPs are intentionally not propagated to the UI.
  ip: '—',
  gpuModel: node.gpu_type || 'Unknown',
  gpuCount: node.num_gpus || 0,
  vramPerGpu: node.total_vram || 0,
  availableVram: roundMetric(node.available_vram),
  status: node.status === 'online' ? 'online' : 'offline',
  load: roundMetric(node.gpu_load),
  gpuLoad: roundMetric(node.gpu_load),
  cpuLoad: roundMetric(node.cpu_load),
  mem: roundMetric(node.mem_usage),
  runningJobs: node.running_jobs || 0,
  sshPort: 22,
});

const REFRESH_INTERVAL_MS = 5000;

const Nodes: React.FC = () => {
  const [nodeList, setNodeList] = useState<ClusterNode[]>([]);
  const [nodesLoading, setNodesLoading] = useState(true);
  const [nodesError, setNodesError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [search, setSearch] = useState('');
  const [sortKey, setSortKey] = useState<NodeSortKey>('name');
  const [actionFeedback, setActionFeedback] = useState<string | null>(null);
  const [credentials, setCredentials] = useState<WorkerCredential[]>([]);
  const [newWorkerId, setNewWorkerId] = useState('');
  const [newSecret, setNewSecret] = useState('');
  const [registering, setRegistering] = useState(false);
  const [registerError, setRegisterError] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;

    const refresh = async () => {
      try {
        const apiNodes = await fetchNodes();
        if (!cancelled) {
          setNodeList(apiNodes.map(toClusterNode));
          setNodesError(null);
          setNodesLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          if (err instanceof UnauthorizedError) {
            navigate('/login', { replace: true });
            return;
          }
          console.error('Failed to load nodes:', err);
          setNodesError(err instanceof Error ? err.message : 'Failed to load nodes.');
          setNodesLoading(false);
        }
      }
    };

    const refreshCredentials = async () => {
      try {
        const creds = await fetchWorkerCredentials();
        if (!cancelled) setCredentials(creds);
      } catch (err) {
        if (!cancelled) {
          if (err instanceof UnauthorizedError) {
            navigate('/login', { replace: true });
            return;
          }
          console.error('Failed to load worker credentials:', err);
        }
      }
    };

    void refresh();
    void refreshCredentials();
    const interval = window.setInterval(refresh, REFRESH_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [navigate]);

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    setRegistering(true);
    setRegisterError(null);
    try {
      await registerWorkerCredential(newWorkerId.trim(), newSecret);
      setNewWorkerId('');
      setNewSecret('');
      const creds = await fetchWorkerCredentials();
      setCredentials(creds);
      showFeedback('Worker credential registered — no restart needed');
    } catch (err) {
      if (err instanceof UnauthorizedError) {
        navigate('/login', { replace: true });
        return;
      }
      setRegisterError(err instanceof Error ? err.message : 'Registration failed.');
    } finally {
      setRegistering(false);
    }
  };

  const handleRevoke = async (workerId: string) => {
    try {
      await revokeWorkerCredential(workerId);
      setCredentials((prev) => prev.filter((c) => c.worker_id !== workerId));
      showFeedback(`${workerId.slice(0, 8)} credential revoked`);
    } catch (err) {
      if (err instanceof UnauthorizedError) {
        navigate('/login', { replace: true });
        return;
      }
      showFeedback(err instanceof Error ? err.message : 'Revoke failed.');
    }
  };

  const visibleNodes = useMemo(() => {
    const filtered = nodeList.filter((n) => {
      if (statusFilter !== 'all' && n.status !== statusFilter) return false;
      if (search.trim()) {
        const q = search.toLowerCase();
        if (!`${n.name} ${n.gpuModel}`.toLowerCase().includes(q)) return false;
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
        case 'name':
        default: return a.name.localeCompare(b.name);
      }
    });
  }, [nodeList, statusFilter, search, sortKey]);

  const showFeedback = (msg: string) => {
    setActionFeedback(msg);
    window.setTimeout(() => setActionFeedback(null), 3000);
  };

  return (
    <div className="fade-in">
      <h1>Cluster Nodes</h1>

      <div className="card" style={{ marginBottom: '1.5rem' }}>
        <h3 style={{ marginTop: 0 }}>Register worker</h3>
        <p style={{ fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
          Paste the worker_id + secret pair issued by the backend owner. It is stored
          in the Scheduler database and takes effect immediately — no file edit or
          restart needed.
        </p>
        <form onSubmit={handleRegister} style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <div className="form-group" style={{ marginBottom: 0, minWidth: 220 }}>
            <label className="form-label">Worker ID</label>
            <input
              className="form-input mono"
              placeholder="6ea7fbeb-…"
              value={newWorkerId}
              onChange={(e) => setNewWorkerId(e.target.value)}
              required
            />
          </div>
          <div className="form-group" style={{ marginBottom: 0, minWidth: 260, flex: 1 }}>
            <label className="form-label">Secret (32–256 chars)</label>
            <input
              type="password"
              className="form-input mono"
              placeholder="paste worker secret"
              value={newSecret}
              onChange={(e) => setNewSecret(e.target.value)}
              required
              minLength={32}
            />
          </div>
          <button type="submit" className="btn btn-primary" disabled={registering}>
            <Plus size={16} />
            {registering ? 'Registering…' : 'Register'}
          </button>
        </form>
        {registerError && (
          <div style={{ marginTop: '0.75rem', fontSize: '0.875rem', color: 'var(--status-failed)' }}>
            {registerError}
          </div>
        )}
        {credentials.length > 0 && (
          <div style={{ marginTop: '1rem' }}>
            <div className="form-label">Registered credentials ({credentials.length})</div>
            <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
              {credentials.map((c) => (
                <span
                  key={c.worker_id}
                  className="badge"
                  style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem' }}
                  title={`source: ${c.source}, secrets: ${c.num_secrets}`}
                >
                  <span className="mono">{c.worker_id.slice(0, 8)}…</span>
                  <span style={{ color: 'var(--text-secondary)' }}>({c.source})</span>
                  {c.source === 'db' && (
                    <button
                      type="button"
                      onClick={() => handleRevoke(c.worker_id)}
                      title="Revoke credential"
                      style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, display: 'inline-flex' }}
                    >
                      <Trash2 size={14} color="var(--status-failed)" />
                    </button>
                  )}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="toolbar">
        <div className="toolbar-controls">
          <div className="toolbar-group">
            <label className="form-label">Search</label>
            <div style={{ position: 'relative' }}>
              <Search size={16} style={{ position: 'absolute', left: '0.75rem', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-secondary)' }} />
              <input
                className="form-input"
                style={{ width: 220, paddingLeft: '2.25rem', paddingTop: '0.5rem', paddingBottom: '0.5rem' }}
                placeholder="name, GPU..."
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

      {nodesLoading && <p>Loading nodes…</p>}
      {nodesError && !nodesLoading && (
        <p style={{ color: 'var(--status-failed)' }}>{nodesError}</p>
      )}

      <div className="table-container">
        <table>
          <thead>
            <tr>
              <th>Node</th>
              <th>Status</th>
              <th>GPU</th>
              <th>VRAM</th>
              <th>Using VRAM</th>
              <th>Load</th>
              <th>Mem</th>
              <th>Jobs</th>
            </tr>
          </thead>
          <tbody>
            {visibleNodes.length === 0 && !nodesLoading && (
              <tr>
                <td colSpan={8} style={{ textAlign: 'center', color: 'var(--text-secondary)', padding: '2rem' }}>
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
                        {node.id.slice(0, 8)}…
                      </div>
                    </div>
                  </div>
                </td>
                <td>{getStatusBadge(node.status)}</td>
                <td style={{ fontSize: '0.8125rem' }}>{node.gpuModel} ×{node.gpuCount}</td>
                <td>{node.vramPerGpu} GB</td>
                <td style={{ minWidth: 110 }}>
                  <div className="progress-bar-track">
                    <div className="progress-bar-fill vram" style={{ width: `${usingVramPercent(node)}%` }} />
                  </div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginTop: '0.25rem' }}>
                    {usingVramPercent(node)}% used
                  </div>
                </td>
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
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default Nodes;