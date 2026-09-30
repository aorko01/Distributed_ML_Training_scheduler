import React from 'react'
import { CheckCircle2, ListVideo, Radio, XCircle } from 'lucide-react'
import { formatDuration, type JobRecord } from '../types'

interface JobsTableProps {
  jobs: JobRecord[]
  connected: boolean
  title?: string
  description?: string
  emptyMessage?: string
}

const STATUS_BADGE: Record<JobRecord['status'], string> = {
  running: 'badge-running',
  completed: 'badge-success',
  failed: 'badge-failed'
}

const JobsTable: React.FC<JobsTableProps> = ({
  jobs,
  connected,
  title = 'Jobs',
  description,
  emptyMessage = 'No jobs to show.'
}) => (
  <section className="card jobs-card">
    <div className="card-header jobs-card-header">
      <div className="section-icon">
        <ListVideo size={19} />
      </div>
      <div className="section-heading">
        <h3>{title}</h3>
        {description ? <p>{description}</p> : null}
      </div>
      <span className="jobs-count mono">{jobs.length}</span>
    </div>

    {connected && jobs.length > 0 ? (
      <div className="table-container jobs-table-wrap">
        <table>
          <thead>
            <tr>
              <th>Job ID</th>
              <th>Image</th>
              <th>Type</th>
              <th>VRAM Est.</th>
              <th>Status</th>
              <th>Phase</th>
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
                    {job.status === 'running' ? <Radio size={12} /> : null}
                    {job.status === 'completed' ? <CheckCircle2 size={12} /> : null}
                    {job.status === 'failed' ? <XCircle size={12} /> : null}
                    {job.status}
                  </span>
                </td>
                <td className="mono">{job.phase ?? '—'}</td>
                <td className="mono">{job.startedAt}</td>
                <td className="mono">{formatDuration(job.durationSec)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    ) : (
      <div className="empty-state jobs-empty-state">
        {connected ? <CheckCircle2 size={28} /> : <XCircle size={28} />}
        <strong>{connected ? emptyMessage : 'Worker service is offline.'}</strong>
        <span>
          {connected
            ? 'This view updates automatically when job activity changes.'
            : 'Job activity will return when the local worker API is available.'}
        </span>
      </div>
    )}
  </section>
)

export default JobsTable
