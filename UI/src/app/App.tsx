import React, { useState } from 'react';
import { LandingPage } from './pages/LandingPage';
import { ResearcherDashboard } from './pages/ResearcherDashboard';
import { Home } from 'lucide-react';

export default function App() {
  const [view, setView] = useState('landing');

  return (
    <div className="relative">
      {view === 'researcher' ? (
        <ResearcherDashboard />
      ) : (
        <LandingPage onSelectRole={setView} />
      )}

      {view !== 'landing' && (
        <button
          onClick={() => setView('landing')}
          className="fixed bottom-4 right-4 z-50 bg-black/80 backdrop-blur-sm border border-white/10 text-white p-3 rounded-full hover:bg-white/10 transition-colors shadow-lg group"
          title="Return to Landing Page"
        >
          <Home size={20} className="group-hover:scale-110 transition-transform" />
        </button>
      )}
    </div>
  );
}
