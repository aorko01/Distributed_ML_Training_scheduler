import React from 'react';
import { motion } from 'motion/react';
import { Cpu, Activity, ArrowRight } from 'lucide-react';

export const LandingPage = ({ onSelectRole }: { onSelectRole: (role: string) => void }) => {
  return (
    <div className="min-h-screen bg-[#0a0a0a] flex flex-col items-center justify-center p-8 relative overflow-hidden">
      {/* Background Elements */}
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,_var(--tw-gradient-stops))] from-[#00ff41]/10 via-[#0a0a0a] to-[#0a0a0a]" />

      <motion.div
        initial={{ y: -20, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        className="relative z-10 text-center mb-16"
      >
        <div className="flex justify-center mb-6">
          <div className="w-16 h-16 bg-[#00ff41]/20 rounded-2xl flex items-center justify-center border border-[#00ff41]/40 shadow-[0_0_30px_rgba(0,255,65,0.2)]">
            <Cpu size={32} className="text-[#00ff41]" />
          </div>
        </div>
        <h1 className="text-5xl font-bold text-white mb-4 tracking-tight">DistributeML</h1>
        <p className="text-gray-400 text-lg max-w-xl mx-auto">
          Distributed GPU Job Scheduling & Resource Management Platform
        </p>
      </motion.div>

      <div className="relative z-10 max-w-md w-full">
        {/* Researcher */}
        <motion.button
          whileHover={{ y: -5, borderColor: '#00ff41' }}
          onClick={() => onSelectRole('researcher')}
          className="group w-full bg-[#121212] border border-[#333] p-8 rounded-2xl text-left transition-all hover:shadow-[0_0_30px_rgba(0,255,65,0.1)]"
        >
          <div className="bg-[#1a1a1a] w-12 h-12 rounded-lg flex items-center justify-center mb-6 group-hover:bg-[#00ff41]/20 transition-colors">
            <Activity className="text-gray-400 group-hover:text-[#00ff41]" />
          </div>
          <h2 className="text-xl font-bold text-white mb-2">Researcher Portal</h2>
          <p className="text-sm text-gray-500 mb-6">
            Submit jobs, track experiments, and visualize training metrics in real-time.
          </p>
          <div className="flex items-center text-[#00ff41] text-sm font-medium opacity-0 group-hover:opacity-100 transition-opacity">
            Launch Dashboard <ArrowRight size={16} className="ml-2" />
          </div>
        </motion.button>
      </div>

      <footer className="absolute bottom-8 text-center text-gray-600 text-xs">
        &copy; 2024 DistributeML Inc. // System v2.4.1-beta
      </footer>
    </div>
  );
};
