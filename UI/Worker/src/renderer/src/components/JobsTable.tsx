import React from 'react'
import { CheckCircle2, ListVideo, XCircle } from 'lucide-react'
import { formatDuration, type JobRecord } from '../data/mock'

interface JobsTableProps {
  jobs: JobRecord[]
  connected: boolean
}

const STATUS_BADGE: Record<JobRecord['status'], string> = {
  running: 'badge-running',
  completed: 'badge-success',
  failed: 'badge-failed'
}

const JobsTable: React.FC<JobsTableProps> = ({ jobs, connected }) => (
  <div className="card">
    <div className="card-header">
      <ListVideo size={20} color="var(--text-secondary)" />
      <h3 style={{ margin: 0 }}>Recent Jobs</h3>
    </div>

    {connected ? (
      <div className="table-container" style={{ marginTop: '1rem' }}>
        <table>
          <thead>
            <tr>
              <th>Job ID</th>
              <th>Image</th>
              <th>Type</th>
              <th>VRAM Est.</th>
              <th>Status</th>
              <th>Started</th>
              <th>Duration</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id}>
                <td className="mono">{job.id}</td>
                <td className="mono">{job.image}</td>
                <td>{job.type}</td>
                <td>{job.vramEstimateGb > 0 ? `${job.vramEstimateGb} GB` : '—'}</td>
                <td>
                  <span className={`badge ${STATUS_BADGE[job.status]}`}>
                    {job.status === 'completed' ? <CheckCircle2 size={12} /> : null}
                    {job.status === 'failed' ? <XCircle size={12} /> : null}
                    {job.status}
                  </span>
                </td>
                <td className="mono">{job.startedAt}</td>
                <td className="mono">{formatDuration(job.durationSec)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    ) : (
      <div className="empty-state" style={{ padding: '2rem' }}>
        <XCircle size={28} />
        <span>No job history available while disconnected.</span>
      </div>
    )}
  </div>
)

export default JobsTable
