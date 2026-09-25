import React, { useEffect, useState } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import Layout from './components/Layout';
import Login from './pages/Login';
import Overview from './pages/Overview';
import Nodes from './pages/Nodes';
import Users from './pages/Users';
import JobQueue from './pages/JobQueue';
import { isAuthenticated, fetchMe } from './services/auth';
import './index.css';

const ProtectedRoute = ({ children }: { children: React.ReactNode }) => {
  const [state, setState] = useState<'checking' | 'ok' | 'denied'>(
    isAuthenticated() ? 'checking' : 'denied',
  );

  useEffect(() => {
    if (!isAuthenticated()) {
      setState('denied');
      return;
    }
    let cancelled = false;
    fetchMe()
      .then((me) => {
        if (cancelled) return;
        setState(me.is_superuser ? 'ok' : 'denied');
      })
      .catch(() => {
        if (!cancelled) setState('denied');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (state === 'checking') {
    return (
      <div style={{ display: 'flex', minHeight: '100vh', alignItems: 'center', justifyContent: 'center' }}>
        <p>Verifying admin session…</p>
      </div>
    );
  }
  if (state === 'denied') {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
};

function App() {
  return (
    <Router>
      <Routes>
        <Route path="/login" element={<Login />} />

        <Route
          path="/"
          element={
            <ProtectedRoute>
              <Layout />
            </ProtectedRoute>
          }
        >
          <Route index element={<Overview />} />
          <Route path="nodes" element={<Nodes />} />
          <Route path="users" element={<Users />} />
          <Route path="jobs" element={<JobQueue />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Router>
  );
}

export default App;
