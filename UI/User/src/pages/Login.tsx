import React, { useState, useEffect } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { login, isAuthenticated } from '../services/auth';
import { Boxes, Check } from 'lucide-react';

const Login: React.FC = () => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    if (isAuthenticated()) {
      navigate('/');
    }
  }, [navigate]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');

    try {
      await login(username, password);
      navigate('/');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Invalid username or password.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-shell">
      <section className="auth-brand">
        <div className="auth-brand-mark">
          <span className="brand-mark"><Boxes size={20} /></span>
          <span className="auth-wordmark">DistributeML</span>
        </div>

        <div className="auth-hero">
          <div className="auth-bigmark">ML</div>
          <h1>Distributed compute, orchestrated.</h1>
          <p>
            Schedule training jobs across a fleet of GPU workers, spin up
            interactive sessions, and watch every log stream in real time.
          </p>
          <div className="auth-feature-list">
            <div className="auth-feature"><span className="tick"><Check size={11} /></span> Queue &amp; prioritize PyTorch / CUDA workloads</div>
            <div className="auth-feature"><span className="tick"><Check size={11} /></span> Live interactive notebooks on dedicated accelerators</div>
            <div className="auth-feature"><span className="tick"><Check size={11} /></span> Per-worker GPU, VRAM &amp; disk telemetry</div>
          </div>
        </div>

        <div className="eyebrow">v0.0.0 · INFRASTRUCTURE CONSOLE</div>
      </section>

      <section className="auth-panel">
        <div className="auth-card fade-in">
          <h1>Sign in</h1>
          <p className="sub">Access your jobs and compute resources.</p>

          {error && (
            <div className="alert alert-error" style={{ marginBottom: '1.25rem' }}>
              {error}
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
              style={{ width: '100%', marginTop: '0.5rem' }}
              disabled={loading}
            >
              {loading ? 'Signing in…' : 'Sign in'}
            </button>

            <div className="auth-footer-note">
              No account? <Link to="/register">Create one</Link>
            </div>
          </form>
        </div>
      </section>
    </div>
  );
};

export default Login;
