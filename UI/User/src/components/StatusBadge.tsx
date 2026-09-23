import React from 'react';
import type { JobStatus } from '../services/jobs';

/** Human labels for the job statuses shown in badges and filters. */
const STATUS_LABELS: Record<JobStatus, string> = {
  Pending: 'Queued',
  Building: 'Building',
  ImageReady: 'Image ready',
  Estimating: 'Estimating VRAM',
  Running: 'Training',
  Retrying: 'Retrying',
  Completed: 'Completed',
  Failed: 'Failed',
};

const STATUS_CLASSES: Record<JobStatus, string> = {
  Pending: 'badge badge-pending',
  Building: 'badge badge-building',
  ImageReady: 'badge badge-ready',
  Estimating: 'badge badge-building',
  Running: 'badge badge-running',
  Retrying: 'badge badge-retrying',
  Completed: 'badge badge-success',
  Failed: 'badge badge-failed',
};

interface StatusBadgeProps {
  status: string;
}

/** Status pill shared by the dashboard, job and build views. */
const StatusBadge: React.FC<StatusBadgeProps> = ({ status }) => {
  const label = STATUS_LABELS[status as JobStatus];
  if (!label) return null;
  return <span className={STATUS_CLASSES[status as JobStatus]}>{label}</span>;
};

export default StatusBadge;
