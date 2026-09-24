import React from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import Layout from './components/Layout';
import Login from './pages/Login';
import Register from './pages/Register';
import Profile from './pages/Profile';
import Dashboard from './pages/Dashboard';
import SubmitJob from './pages/SubmitJob';
import Builds from './pages/Builds';
import BuildDetails from './pages/BuildDetails';
import InteractiveDetails from './pages/InteractiveDetails';
import InteractiveWorkspaces from './pages/InteractiveWorkspaces';
import InteractiveEditor from './pages/InteractiveEditor';
import JobDetails from './pages/JobDetails';
import Training from './pages/Training';
import { isAuthenticated } from './services/auth';
import './index.css';

// Protected Route Wrapper
const ProtectedRoute = ({ children }: { children: React.ReactNode }) => {
  if (!isAuthenticated()) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
};

function App() {
  return (
    <Router>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        
        <Route path="/" element={
          <ProtectedRoute>
            <Layout />
          </ProtectedRoute>
        }>
          <Route index element={<Dashboard />} />
          <Route path="submit" element={<SubmitJob />} />
          <Route path="training" element={<Training />} />
          <Route path="builds" element={<Builds />} />
          <Route path="builds/:id" element={<BuildDetails />} />
          <Route path="interactive" element={<InteractiveWorkspaces />} />
          <Route path="interactive/:id" element={<InteractiveDetails />} />
          <Route path="interactive/:id/editor" element={<InteractiveEditor />} />
          <Route path="machines" element={<Navigate to="/interactive" replace />} />
          <Route path="jobs/:id" element={<JobDetails />} />
          <Route path="profile" element={<Profile />} />
        </Route>
        
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Router>
  );
}

export default App;
