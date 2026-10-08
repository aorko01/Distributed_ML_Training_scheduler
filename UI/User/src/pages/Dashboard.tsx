import React, { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { fetchClusterStats, type ClusterStats } from "../services/stats";
import { fetchJobs, type Job } from "../services/jobs";
import StatusBadge from "../components/StatusBadge";
import {
  Activity,
  Clock,
  Cpu,
  Loader2,
  ArrowRight,
  ArrowUpRight,
  Plus,
  Boxes,
  Terminal,
  Rocket,
} from "lucide-react";
import ComputeArtwork from "../components/ComputeArtwork";
import { getUsername } from "../services/auth";

type ActiveTrainingStatus = "Estimating" | "Running" | "Retrying";
type StatusFilter = "All" | ActiveTrainingStatus;
type SortKey = "newest" | "oldest" | "name" | "gpuHours";

const ACTIVE_TRAINING_STATUSES: ActiveTrainingStatus[] = [
  "Estimating",
  "Running",
  "Retrying",
];
const REFRESH_INTERVAL_MS = 5_000;

const Dashboard: React.FC = () => {
  const [stats, setStats] = useState<ClusterStats | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("All");
  const [sortBy, setSortBy] = useState<SortKey>("newest");
  const navigate = useNavigate();

  useEffect(() => {
    let active = true;

    const loadInitialData = async () => {
      try {
        const [statsData, jobsData] = await Promise.all([
          fetchClusterStats(),
          fetchJobs(),
        ]);
        if (!active) return;
        setStats(statsData);
        setJobs(jobsData);
      } finally {
        if (active) setLoading(false);
      }
    };

    const refreshJobs = async () => {
      const jobsData = await fetchJobs();
      if (active) setJobs(jobsData);
    };

    void loadInitialData();
    const interval = window.setInterval(() => {
      void refreshJobs();
    }, REFRESH_INTERVAL_MS);

    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, []);

  const activeJobs = useMemo(
    () =>
      jobs.filter((job) =>
        (ACTIVE_TRAINING_STATUSES as string[]).includes(job.status),
      ),
    [jobs],
  );

  const visibleJobs = useMemo(() => {
    const filtered =
      statusFilter === "All"
        ? activeJobs
        : activeJobs.filter((job) => job.status === statusFilter);

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
  }, [activeJobs, statusFilter, sortBy]);

  const runningCount = activeJobs.filter(
    (job) => job.status === "Running",
  ).length;
  const trainingCount = activeJobs.length;
  const username = getUsername() || "researcher";

  if (loading) {
    return (
      <div className="empty-state" role="status">
        <Loader2 className="animate-spin" size={28} />
        <p>Opening your studio…</p>
      </div>
    );
  }

  return (
    <div className="fade-in">
      <div className="dashboard-welcome">
        <div>
          <span className="eyebrow">Your personal compute workspace</span>
          <h1>
            Make room for <span className="welcome-accent">what’s next.</span>
          </h1>
          <p>Welcome back, {username}. Here’s where your experiments stand.</p>
        </div>
        <Link to="/submit" className="btn btn-primary">
          <Plus size={15} /> New workspace
        </Link>
      </div>
      <div className="overview-hero">
        <div>
          <span className="eyebrow">From idea to iteration</span>
          <h2>
            Your code.
            <br />
            <span>A whole cluster of possibility.</span>
          </h2>
          <p>
            Build your environment, find the right compute, and let your next
            experiment take shape.
          </p>
          <Link to="/training" className="text-link">
            Start a training run <ArrowRight size={14} />
          </Link>
        </div>
        <ComputeArtwork compact />
      </div>
      {stats && (
        <div className="metrics-grid">
          <div className="metric-card">
            <div className="metric-top">
              <span className="metric-title">In the queue</span>
              <Activity size={15} />
            </div>
            <div className="metric-value">
              {stats.queueLength.toString().padStart(2, "0")}
            </div>
            <p className="metric-caption">Waiting for compute</p>
          </div>
          <div className="metric-card">
            <div className="metric-top">
              <span className="metric-title">GPU time</span>
              <Clock size={15} />
            </div>
            <div className="metric-value">
              {stats.gpuHoursUsed.toFixed(1)}
              <span>hrs</span>
            </div>
            <p className="metric-caption">Your accumulated usage</p>
          </div>
          <div className="metric-card">
            <div className="metric-top">
              <span className="metric-title">Running now</span>
              <Rocket size={15} />
            </div>
            <div className="metric-value">
              {runningCount.toString().padStart(2, "0")}
            </div>
            <p className="metric-caption">Active training runs</p>
          </div>
          <div className="metric-card">
            <div className="metric-top">
              <span className="metric-title">Cluster GPUs</span>
              <Cpu size={15} />
            </div>
            <div className="metric-value">
              {stats.totalNodes.toString().padStart(2, "0")}
            </div>
            <p className="metric-caption">Across the cluster</p>
          </div>
        </div>
      )}
      <div className="dashboard-toolbar">
        <div>
          <h2>
            Active training
            <span>{trainingCount.toString().padStart(2, "0")} runs</span>
          </h2>
          <p>
            Every run is a step forward. Follow your active experiments here.
          </p>
        </div>
        <div className="builds-filters">
          <label htmlFor="dashboard-filter">
            Status
            <select
              id="dashboard-filter"
              className="form-select"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
            >
              <option value="All">All training</option>
              <option value="Estimating">Estimating VRAM</option>
              <option value="Running">Training</option>
              <option value="Retrying">Retrying</option>
            </select>
          </label>
          <label htmlFor="dashboard-sort">
            Order
            <select
              id="dashboard-sort"
              className="form-select"
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as SortKey)}
            >
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
              <option value="name">Name (A–Z)</option>
              <option value="gpuHours">GPU hours</option>
            </select>
          </label>
        </div>
      </div>
      <div className="table-container">
        <table aria-label="Training jobs">
          <thead>
            <tr>
              <th>Experiment</th>
              <th>Status</th>
              <th>Environment</th>
              <th>Device</th>
              <th>Submitted</th>
              <th>GPU hrs</th>
            </tr>
          </thead>
          <tbody>
            {visibleJobs.length === 0 && (
              <tr>
                <td colSpan={6}>
                  <div className="empty-state">
                    <Rocket size={26} />
                    <h3>
                      {trainingCount === 0
                        ? "The next experiment is yours."
                        : "No runs with this status."}
                    </h3>
                    <p>
                      {trainingCount === 0
                        ? "Start with a built image, then send your first training run to the cluster."
                        : "Choose another status to see more of your training journal."}
                    </p>
                    <Link to="/training" className="btn btn-secondary">
                      Start training <ArrowRight size={14} />
                    </Link>
                  </div>
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
                  <span className="job-id-sub">
                    {job.id.slice(0, 12)}
                    {(job.trainingEligible === false ||
                      job.sourceKind === "PACKAGES_ONLY") &&
                      " · Interactive only"}
                  </span>
                </td>
                <td>
                  <StatusBadge status={job.status} />
                </td>
                <td className="table-mono">
                  PT {job.pytorchVersion} / CUDA {job.cudaVersion}
                </td>
                <td className="table-mono">
                  {job.status === "Running" || job.status === "Completed"
                    ? job.device
                    : "—"}
                </td>
                <td className="table-mono">
                  {new Date(job.submittedAt).toLocaleDateString(undefined, {
                    day: "2-digit",
                    month: "short",
                  })}
                  <span className="job-id-sub">
                    {new Date(job.submittedAt).toLocaleTimeString(undefined, {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </span>
                </td>
                <td className="table-mono">{job.gpuHours.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="journal-history-link">
        <Link to="/job-history" className="text-link">
          View completed runs and job history <ArrowRight size={14} />
        </Link>
      </div>
      <div className="quick-links">
        <Link to="/builds" className="quick-link">
          <Boxes size={18} />
          <div>
            <strong>Image library</strong>
            <p>A foundation for every experiment.</p>
          </div>
          <ArrowUpRight size={14} />
        </Link>
        <Link to="/training" className="quick-link">
          <Rocket size={18} />
          <div>
            <strong>Start training</strong>
            <p>Put your next idea in motion.</p>
          </div>
          <ArrowUpRight size={14} />
        </Link>
        <Link to="/interactive" className="quick-link">
          <Terminal size={18} />
          <div>
            <strong>Explore interactively</strong>
            <p>Get closer to your code.</p>
          </div>
          <ArrowUpRight size={14} />
        </Link>
      </div>
    </div>
  );
};

export default Dashboard;
