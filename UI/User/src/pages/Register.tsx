import React, { useState, useEffect } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { register, isAuthenticated } from '../services/auth';
import { Boxes, Check } from 'lucide-react';

const Register: React.FC = () => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
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
      await register(username, password, name, email);
      navigate('/');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Registration failed. Please try again.');
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
          <h1>Create account</h1>
          <p className="sub">Spin up your workspace in seconds.</p>

          {error && (
            <div className="alert alert-error" style={{ marginBottom: '1.25rem' }}>
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit}>
            <div className="form-group">
              <label className="form-label">Full Name</label>
              <input
                type="text"
                className="form-input"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Ada Lovelace"
                required
              />
            </div>
            <div className="form-group">
              <label className="form-label">Email</label>
              <input
                type="email"
                className="form-input"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="ada@example.com"
                required
              />
            </div>
            <div className="form-group">
              <label className="form-label">Username</label>
              <input
                type="text"
                className="form-input"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="ada"
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
                minLength={6}
              />
            </div>
            <button
              type="submit"
              className="btn btn-primary"
              style={{ width: '100%', marginTop: '0.5rem' }}
              disabled={loading}
            >
              {loading ? 'Creating account…' : 'Create account'}
            </button>

            <div className="auth-footer-note">
              Already registered? <Link to="/login">Sign in</Link>
            </div>
          </form>
        </div>
      </section>
    </div>
  );
};

export default Register;
