import React, { useState, useEffect } from 'react';
import { useNavigate, Link, useSearchParams } from 'react-router-dom';
import { login, isAuthenticated, isBlockedError } from '../services/auth';
import { Activity, Ban } from 'lucide-react';

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
    <div style={{ display: 'flex', minHeight: '100vh', alignItems: 'center', justifyContent: 'center' }}>
      <div className="card glass fade-in" style={{ width: '100%', maxWidth: '400px' }}>
        <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
          <Activity size={48} color="var(--accent-primary)" style={{ margin: '0 auto' }} />
          <h1 style={{ marginTop: '1rem', marginBottom: '0.5rem' }}>DistributeML</h1>
          <p>Login to manage your ML tasks</p>
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
              placeholder="admin"
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
    </div>
  );
};

export default Login;
