import React, { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { fetchJobs, type Job } from "../services/jobs";
import StatusBadge from "../components/StatusBadge";
import PageHeading from "../components/PageHeading";
import { Loader2, Search } from "lucide-react";

type HistoryStatus = "Completed" | "Failed";
type StatusFilter = "All" | HistoryStatus;
type SortKey = "newest" | "oldest" | "name" | "gpuHours";

const HISTORY_STATUSES: HistoryStatus[] = ["Completed", "Failed"];

const JobHistory: React.FC = () => {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("All");
  const [sortBy, setSortBy] = useState<SortKey>("newest");
  const navigate = useNavigate();

  useEffect(() => {
    let active = true;
    const loadJobs = async () => {
      try {
        const jobsData = await fetchJobs();
        if (active) setJobs(jobsData);
      } finally {
        if (active) setLoading(false);
      }
    };

    void loadJobs();
    return () => {
      active = false;
    };
  }, []);

  const visibleJobs = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    const filtered = jobs.filter((job) => {
      if (!(HISTORY_STATUSES as string[]).includes(job.status)) return false;
      if (statusFilter !== "All" && job.status !== statusFilter) return false;
      if (
        normalizedQuery &&
        !`${job.name} ${job.id}`.toLowerCase().includes(normalizedQuery)
      )
        return false;
      return true;
    });

    return [...filtered].sort((a, b) => {
      switch (sortBy) {
        case "oldest":
          return (
            new Date(a.submittedAt).getTime() -
            new Date(b.submittedAt).getTime()
          );
        case "name":
          return a.name.localeCompare(b.name);
        case "gpuHours":
          return b.gpuHours - a.gpuHours;
        case "newest":
        default:
          return (
            new Date(b.submittedAt).getTime() -
            new Date(a.submittedAt).getTime()
          );
      }
    });
  }, [jobs, query, statusFilter, sortBy]);

  if (loading) {
    return (
      <div
        style={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "100%",
        }}
      >
        <Loader2 className="animate-spin text-accent" size={32} />
      </div>
    );
  }

  return (
    <div className="fade-in">
      <PageHeading
        eyebrow="05 / The training journal"
        title="Every run leaves a mark."
        description="Your completed and failed experiments, their final status, and the compute that brought them here."
      />

      <div className="card builds-toolbar history-toolbar">
        <label className="builds-search">
          <span className="form-label">Search jobs</span>
          <span className="iw-search">
            <Search size={17} aria-hidden="true" />
            <input
              className="form-input"
              placeholder="Search by name or job id..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </span>
        </label>
        <div className="builds-filters">
          <label>
            Status
            <select
              className="form-select"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
            >
              <option value="All">All results</option>
              <option value="Completed">Completed</option>
              <option value="Failed">Failed</option>
            </select>
          </label>
          <label>
            Sort by
            <select
              className="form-select"
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as SortKey)}
            >
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
              <option value="name">Name (A-Z)</option>
              <option value="gpuHours">GPU hours</option>
            </select>
          </label>
        </div>
      </div>

      <div className="table-container">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Final Status</th>
              <th>Environment</th>
              <th>Device</th>
              <th>Submitted At</th>
              <th>GPU Hrs</th>
            </tr>
          </thead>
          <tbody>
            {visibleJobs.length === 0 && (
              <tr>
                <td colSpan={6} className="jobs-empty-cell">
                  No completed or failed jobs match these filters.
                </td>
              </tr>
            )}
            {visibleJobs.map((job) => (
              <tr
                key={job.id}
                className="job-row"
                onClick={() => navigate(`/jobs/${job.id}`)}
              >
                <td>
                  <Link
                    className="job-name-link"
                    to={`/jobs/${job.id}`}
                    onClick={(e) => e.stopPropagation()}
                  >
                    {job.name}
                  </Link>
                  <span className="job-id-sub">{job.id}</span>
                </td>
                <td>
                  <StatusBadge status={job.status} />
                </td>
                <td>
                  PT {job.pytorchVersion} / CUDA {job.cudaVersion}
                </td>
                <td>
                  <span className="table-mono">{job.device}</span>
                </td>
                <td>{new Date(job.submittedAt).toLocaleString()}</td>
                <td>{job.gpuHours.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default JobHistory;
