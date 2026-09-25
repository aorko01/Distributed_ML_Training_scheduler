import React, { useState, useEffect } from 'react';
import { useNavigate, Link, useSearchParams } from 'react-router-dom';
import { login, isAuthenticated, isBlockedError } from '../services/auth';
import { Activity, Ban, Boxes, Gauge, Rocket, SquarePen } from 'lucide-react';

const BLOCKED_MESSAGE = 'Your account has been blocked. Contact an administrator.';

const Login: React.FC = () => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [blocked, setBlocked] = useState(false);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  useEffect(() => {
    if (isAuthenticated()) {
      navigate('/');
      return;
    }
    // Arriving here via the auth layer means the account was disabled
    // mid-session (token revoked server-side).
    if (searchParams.get('blocked') === '1') {
      setBlocked(true);
      setError(BLOCKED_MESSAGE);
    }
  }, [navigate, searchParams]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    setBlocked(false);

    try {
      await login(username, password);
      navigate('/');
    } catch (err) {
      if (isBlockedError(err) || (err instanceof Error && /blocked|disabled/i.test(err.message))) {
        setBlocked(true);
        setError(BLOCKED_MESSAGE);
      } else {
        setError(err instanceof Error ? err.message : 'Invalid username or password.');
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-page fade-in">
      <aside className="auth-showcase" aria-hidden="true">
        <div className="auth-brand">
          <Activity size={26} color="var(--accent-primary)" />
          <span>DistributeML</span>
        </div>
        <h1>
          Ship models,<br />not YAML.
        </h1>
        <p className="auth-tagline">
          Your GPUs are already warm. Build once, estimate VRAM in one click,
          and train at cluster scale.
        </p>
        <ul className="auth-points">
          <li><Boxes size={16} /><span><strong>Build once</strong> — PyTorch + CUDA images from a zip</span></li>
          <li><Gauge size={16} /><span><strong>Know before you go</strong> — VRAM estimates before training</span></li>
          <li><Rocket size={16} /><span><strong>Train at scale</strong> — queued, retried, and tracked</span></li>
          <li><SquarePen size={16} /><span><strong>Debug live</strong> — browser editor or VS Code Remote-SSH</span></li>
        </ul>
        <p className="auth-foot">Trusted for overnight runs and deadline-day retries.</p>
      </aside>

      <main className="auth-form-side">
        <div className="card glass auth-card">
          <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
            <Activity size={40} color="var(--accent-primary)" style={{ margin: '0 auto' }} />
            <h1 style={{ marginTop: '1rem', marginBottom: '0.5rem' }}>Welcome back</h1>
            <p>Login to fire up your next run</p>
          </div>

          {error && (
            <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'flex-start', padding: '0.75rem', backgroundColor: blocked ? 'rgba(239, 68, 68, 0.15)' : 'rgba(239, 68, 68, 0.1)', color: blocked ? '#fca5a5' : 'var(--status-failed)', border: blocked ? '1px solid rgba(239, 68, 68, 0.4)' : 'none', borderRadius: '6px', marginBottom: '1rem', fontSize: '0.875rem' }}>
              {blocked && <Ban size={16} style={{ flexShrink: 0, marginTop: '0.125rem' }} />}
              <span>{error}</span>
            </div>
          )}

          <form onSubmit={handleSubmit}>
            <div className="form-group">
              <label className="form-label">Username</label>
              <input
                type="text"
                className="form-input"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="admin"
                required
              />
            </div>
            <div className="form-group">
              <label className="form-label">Password</label>
              <input
                type="password"
                className="form-input"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                required
              />
            </div>
            <button
              type="submit"
              className="btn btn-primary"
              style={{ width: '100%', marginBottom: '1rem' }}
              disabled={loading}
            >
              {loading ? 'Logging in...' : 'Login'}
            </button>

            <div style={{ textAlign: 'center', fontSize: '0.875rem' }}>
              Don't have an account? <Link to="/register" style={{ color: 'var(--accent-primary)', textDecoration: 'none' }}>Register here</Link>
            </div>
          </form>
        </div>
      </main>
    </div>
  );
};

export default Login;
