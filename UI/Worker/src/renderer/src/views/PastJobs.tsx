import React, { useMemo, useState } from 'react'
import { CheckCircle2, History, Search, XCircle } from 'lucide-react'
import JobsTable from '../components/JobsTable'
import TopBar from '../components/TopBar'
import type { WorkerData } from '../hooks/useWorkerData'

type HistoryFilter = 'all' | 'completed' | 'failed'

const PastJobs: React.FC<WorkerData> = ({
  worker,
  jobs,
  connected,
  apiReachable,
  acceptingJobs,
  updatingAcceptingJobs,
  lastHeartbeat,
  toggleAcceptingJobs
}) => {
  const [filter, setFilter] = useState<HistoryFilter>('all')
  const [query, setQuery] = useState('')
  const pastJobs = useMemo(() => jobs.filter((job) => job.status !== 'running'), [jobs])
  const completed = pastJobs.filter((job) => job.status === 'completed').length
  const failed = pastJobs.filter((job) => job.status === 'failed').length
  const visibleJobs = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase()
    return pastJobs.filter((job) => {
      if (filter !== 'all' && job.status !== filter) return false
      if (!normalizedQuery) return true
      return `${job.id} ${job.image} ${job.type} ${job.phase ?? ''}`
        .toLowerCase()
        .includes(normalizedQuery)
    })
  }, [filter, pastJobs, query])

  return (
    <div className="dashboard">
      <TopBar
        connected={connected}
        acceptingJobs={acceptingJobs}
        acceptingJobsDisabled={!apiReachable || updatingAcceptingJobs}
        hostname={worker?.hostname ?? 'DML Worker'}
        ipAddress={worker?.ipAddress ?? '—'}
        lastHeartbeat={lastHeartbeat}
        onToggleAccepting={toggleAcceptingJobs}
      />

      <div className="page-content fade-in">
        <section className="page-intro">
          <div className="page-intro-icon"><History size={22} /></div>
          <div>
            <span className="eyebrow">JOB ARCHIVE</span>
            <h1>Past jobs</h1>
            <p>Review completed and failed assignments previously handled by this worker.</p>
          </div>
        </section>

        <div className="history-summary-grid">
          <div className="history-summary-card">
            <History size={18} />
            <span>Recorded</span>
            <strong>{pastJobs.length}</strong>
          </div>
          <div className="history-summary-card history-success">
            <CheckCircle2 size={18} />
            <span>Completed</span>
            <strong>{completed}</strong>
          </div>
          <div className="history-summary-card history-failed">
            <XCircle size={18} />
            <span>Failed</span>
            <strong>{failed}</strong>
          </div>
        </div>

        <div className="view-toolbar" aria-label="Past job filters">
          <div className="segmented-control">
            {(['all', 'completed', 'failed'] as const).map((value) => (
              <button
                key={value}
                type="button"
                className={filter === value ? 'active' : ''}
                aria-pressed={filter === value}
                onClick={() => setFilter(value)}
              >
                {value === 'all' ? 'All past jobs' : value}
              </button>
            ))}
          </div>
          <label className="search-field">
            <Search size={16} />
            <span className="sr-only">Search past jobs</span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search ID, image, type, or phase"
            />
          </label>
        </div>

        <JobsTable
          jobs={visibleJobs}
          connected={apiReachable}
          title="Job history"
          description={`${visibleJobs.length} of ${pastJobs.length} archived assignments shown.`}
          emptyMessage={pastJobs.length === 0 ? 'No past jobs yet.' : 'No jobs match these filters.'}
        />
      </div>
    </div>
  )
}

export default PastJobs
