import React from 'react';
import { Activity, Layers, Clock } from 'lucide-react';
import type { CapacityPreview } from '../../services/interactive';

export const CapacitySummary: React.FC<{ preview: CapacityPreview | null; loading: boolean }> = ({ preview, loading }) => (
  <div className="config-summary-grid" aria-live="polite">
    <div className="config-summary-item">
      <Layers size={16} color="var(--accent-primary)" />
      <div>
        <div className="config-summary-value">{loading ? '…' : (preview?.matching_online ?? '–')}</div>
        <div className="config-summary-label">Matching online</div>
      </div>
    </div>
    <div className="config-summary-item">
      <Activity size={16} color="var(--accent-primary)" />
      <div>
        <div className="config-summary-value">{loading ? '…' : (preview?.available_now ?? '–')}</div>
        <div className="config-summary-label">Available now</div>
      </div>
    </div>
    <div className="config-summary-item">
      <Clock size={16} color="var(--accent-primary)" />
      <div>
        <div className="config-summary-value">{loading ? '…' : (preview?.busy ?? '–')}</div>
        <div className="config-summary-label">Busy</div>
      </div>
    </div>
    <div className="config-summary-item">
      <Activity size={16} color="var(--accent-primary)" />
      <div>
        <div className="config-summary-value">{loading ? '…' : (preview?.queued_interactive_requests ?? '–')}</div>
        <div className="config-summary-label">Queued requests</div>
      </div>
    </div>
  </div>
);
