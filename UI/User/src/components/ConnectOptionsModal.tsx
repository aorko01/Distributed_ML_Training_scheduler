import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  X,
  Copy,
  Check,
  BookOpen,
  Terminal,
  Zap,
  FolderOpen,
  Package,
  ArrowUpRight,
  Clock,
} from 'lucide-react';
import type { Job } from '../services/jobs';

interface ConnectOptionsModalProps {
  job: Job;
  onClose: () => void;
}

const hashString = (value: string): number => {
  let hash = 0;
  for (let i = 0; i < value.length; i += 1) {
    hash = (hash * 31 + value.charCodeAt(i)) >>> 0;
  }
  return hash;
};

const ConnectOptionsModal: React.FC<ConnectOptionsModalProps> = ({ job, onClose }) => {
  const navigate = useNavigate();
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onClose]);

  const nodeNum = (hashString(job.id) % 8) + 1;
  const sshCommand = `ssh researcher@node-a100-0${nodeNum}.dml.cloud -p 2222`;

  const handleCopySsh = async () => {
    try {
      await navigator.clipboard.writeText(sshCommand);
    } catch {
      // Clipboard unavailable — the command is visible anyway.
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal"
        style={{ maxWidth: 660 }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <span className="live-dot" />
            <div>
              <h3 style={{ margin: 0 }}>Connect to “{job.name}”</h3>
              <p style={{ margin: 0, fontSize: '0.78rem', fontFamily: "'JetBrains Mono', monospace" }}>
                session {job.id.slice(0, 8)} · PyTorch {job.pytorchVersion} · CUDA {job.cudaVersion}
              </p>
            </div>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>

        <div className="modal-body" style={{ paddingTop: '1.25rem' }}>
          <div className="cn-grid">
            <div className="cn-card cn-card-primary">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <div className="cn-icon-tile">
                  <BookOpen size={20} />
                </div>
                <span className="badge badge-running">Recommended</span>
              </div>
              <h4 style={{ margin: 0, fontSize: '1rem' }}>Jupyter Notebook</h4>
              <p style={{ margin: 0, fontSize: '0.82rem' }}>
                A Kaggle-style notebook running on this session's GPU. Code, run and iterate in your browser.
              </p>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.45rem', marginTop: 'auto' }}>
                <div className="cn-feat"><Zap size={13} color="var(--status-pending)" /> GPU-backed kernel, ready in seconds</div>
                <div className="cn-feat"><FolderOpen size={13} color="var(--accent-primary)" /> Persistent /workspace storage</div>
                <div className="cn-feat"><Package size={13} color="var(--status-building)" /> Pre-installed PyTorch stack</div>
              </div>
              <button
                className="btn btn-primary"
                style={{ width: '100%', marginTop: '0.5rem' }}
                onClick={() => navigate(`/notebooks/${job.id}`)}
              >
                Launch Notebook
                <ArrowUpRight size={16} />
              </button>
            </div>

            <div className="cn-card">
              <div className="cn-icon-tile cn-icon-tile-ssh">
                <Terminal size={20} />
              </div>
              <h4 style={{ margin: 0, fontSize: '1rem' }}>SSH Access</h4>
              <p style={{ margin: 0, fontSize: '0.82rem' }}>
                Full shell into the container with your own tooling, editors and tmux sessions.
              </p>
              <div className="cn-cmd">
                <code>{sshCommand}</code>
                <button
                  className="cn-copy-btn"
                  onClick={handleCopySsh}
                  aria-label="Copy SSH command"
                >
                  {copied ? <Check size={14} color="var(--status-success)" /> : <Copy size={14} />}
                </button>
              </div>
              <button
                className="btn btn-secondary"
                style={{ width: '100%', marginTop: 'auto' }}
                onClick={handleCopySsh}
              >
                {copied ? <Check size={16} color="var(--status-success)" /> : <Copy size={16} />}
                {copied ? 'Copied!' : 'Copy Command'}
              </button>
            </div>
          </div>

          <div className="cn-meta-footer">
            <Clock size={13} />
            <span>Session auto-pauses after 60 min idle · Dummy preview — live wiring coming soon</span>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ConnectOptionsModal;
