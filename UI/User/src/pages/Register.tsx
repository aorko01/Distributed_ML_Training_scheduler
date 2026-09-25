import React, { useState, useEffect } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { register, isAuthenticated } from '../services/auth';
import { Activity, Boxes, Gauge, Rocket, SquarePen } from 'lucide-react';

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
    <div className="auth-page fade-in">
      <aside className="auth-showcase" aria-hidden="true">
        <div className="auth-brand">
          <Activity size={26} color="var(--accent-primary)" />
          <span>DistributeML</span>
        </div>
        <h1>
          Claim your<br />compute.
        </h1>
        <p className="auth-tagline">
          One account. Every GPU. From first build to finished checkpoint —
          without the queue anxiety.
        </p>
        <ul className="auth-points">
          <li><Boxes size={16} /><span><strong>30-second start</strong> — upload a zip, get a ready image</span></li>
          <li><Gauge size={16} /><span><strong>No OOM roulette</strong> — VRAM sizing up front</span></li>
          <li><Rocket size={16} /><span><strong>Overnight-proof</strong> — retries and live logs built in</span></li>
          <li><SquarePen size={16} /><span><strong>Fix it live</strong> — jump into any session from VS Code</span></li>
        </ul>
        <p className="auth-foot">Free to join. Built for deadline weeks.</p>
      </aside>

      <main className="auth-form-side">
        <div className="card glass auth-card">
          <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
            <Activity size={40} color="var(--accent-primary)" style={{ margin: '0 auto' }} />
            <h1 style={{ marginTop: '1rem', marginBottom: '0.5rem' }}>Create Account</h1>
            <p>Join up and launch your first run today</p>
          </div>

          {error && (
            <div style={{ padding: '0.75rem', backgroundColor: 'rgba(239, 68, 68, 0.1)', color: 'var(--status-failed)', borderRadius: '6px', marginBottom: '1rem', fontSize: '0.875rem' }}>
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
                placeholder="John Doe"
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
                placeholder="john@example.com"
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
                placeholder="johndoe"
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
              style={{ width: '100%', marginBottom: '1rem' }}
              disabled={loading}
            >
              {loading ? 'Creating account...' : 'Sign Up'}
            </button>

            <div style={{ textAlign: 'center', fontSize: '0.875rem' }}>
              Already have an account? <Link to="/login" style={{ color: 'var(--accent-primary)', textDecoration: 'none' }}>Login here</Link>
            </div>
          </form>
        </div>
      </main>
    </div>
  );
};

export default Register;
