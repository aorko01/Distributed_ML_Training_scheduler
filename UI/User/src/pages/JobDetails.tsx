import React, { useEffect, useRef, useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import {
  downloadJobOutput,
  fetchJobById,
  fetchJobLogs,
  streamJobLogs,
  type Job,
  type JobStatus,
  type LogLine,
} from "../services/jobs";
import LogTerminal from "../components/LogTerminal";
import CopyButton from "../components/CopyButton";
import { ArrowLeft, Download, Loader2, Rocket, MonitorPlay, X } from "lucide-react";
import type { DownloadProgress } from "../services/jobDownload";

const JobDetails: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [job, setJob] = useState<Job | null>(null);
  const [loading, setLoading] = useState(true);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [liveStatus, setLiveStatus] = useState<JobStatus | undefined>(undefined);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [downloadProgress, setDownloadProgress] =
    useState<DownloadProgress | null>(null);
  const downloadAbortRef = useRef<AbortController | null>(null);

  const formatBytes = (bytes: number): string => {
    if (!Number.isFinite(bytes) || bytes < 0) return "0 B";
    if (bytes < 1024) return `${bytes} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let value = bytes / 1024;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
      value /= 1024;
      unit += 1;
    }
    return `${value.toFixed(value >= 100 ? 0 : 1)} ${units[unit]}`;
  };

  const handleDownloadOutput = async () => {
    if (!job || downloading) return;

    const controller = new AbortController();
    downloadAbortRef.current = controller;
    setDownloading(true);
    setDownloadError(null);
    setDownloadProgress({ phase: "preparing", loadedBytes: 0, totalBytes: null });
    try {
      await downloadJobOutput(job.id, job.name, {
        signal: controller.signal,
        onProgress: (progress) => setDownloadProgress({ ...progress }),
      });
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        // Cancelled by the user; not an error worth surfacing.
        return;
      }
      if (err instanceof Error && err.name === "AbortError") return;
      setDownloadError(
        err instanceof Error ? err.message : "Failed to download output.",
      );
    } finally {
      downloadAbortRef.current = null;
      setDownloading(false);
      setDownloadProgress(null);
    }
  };

  const handleCancelDownload = () => {
    downloadAbortRef.current?.abort();
  };

  useEffect(() => {
    return () => {
      downloadAbortRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (!id) return;

    const loadJob = async () => {
      const data = await fetchJobById(id);
      setJob(data || null);
      setLoading(false);
    };

    loadJob();
  }, [id]);

  useEffect(() => {
    if (!id || !job) return;

    let cancelled = false;
    let stopStream: (() => void) | undefined;

    const isFinished = job.status === "Completed" || job.status === "Failed";

    const load = async () => {
      setLogs([]);

      if (isFinished) {
        // Finished jobs: training logs come purely from the object store
        // ({job_id}/training.log, with a build.log fallback for legacy jobs).
        const stored = await fetchJobLogs(id);
        if (!cancelled) setLogs(stored);
        return;
      }

      // Running jobs: the socket sends the training Redis stream history
      // first, then live lines. Build output streams separately on the
      // Builds page and never lands in this terminal.
      stopStream = streamJobLogs(id, {
        onLog: (line) => {
          if (!cancelled) setLogs((prev) => [...prev, line]);
        },
        onDone: (status) => {
          if (cancelled) return;
          if (status === "COMPLETED") setLiveStatus("Completed");
          else if (status === "FAILED") setLiveStatus("Failed");
          else if (status === "RETRY_NEEDED") setLiveStatus("Retrying");
        },
      });
    };

    load();

    return () => {
      cancelled = true;
      stopStream?.();
    };
  }, [id, job]);

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
        <Loader2 className="animate-spin text-blue-500" size={32} />
      </div>
    );
  }

  if (!job) {
    return (
      <div className="fade-in">
        <h2>Job not found</h2>
        <button className="btn btn-secondary" onClick={() => navigate("/")}>
          Back to Dashboard
        </button>
      </div>
    );
  }

  const getStatusBadge = (status: string) => {    switch (status) {
      case "Pending":
        return <span className="badge badge-pending">Queued</span>;
      case "Building":
        return <span className="badge badge-building">Building</span>;
      case "ImageReady":
        return <span className="badge badge-ready">Image ready</span>;
      case "Estimating":
        return <span className="badge badge-building">Estimating VRAM</span>;
      case "Running":
        return <span className="badge badge-running">Training</span>;
      case "Retrying":
        return <span className="badge badge-retrying">Retrying</span>;
      case "Completed":
        return <span className="badge badge-success">Completed</span>;
      case "Failed":
        return <span className="badge badge-failed">Failed</span>;
      default:
        return null;
    }
  };

  return (
    <div
      className="fade-in"
      style={{ display: "flex", flexDirection: "column", height: "100%" }}
    >
      <div
        style={{
          marginBottom: "2rem",
          display: "flex",
          alignItems: "center",
          gap: "1rem",
        }}
      >
        <button
          className="btn btn-secondary"
          style={{ padding: "0.5rem", borderRadius: "50%" }}
          onClick={() => navigate("/")}
        >
          <ArrowLeft size={20} />
        </button>
        <h1 style={{ margin: 0 }}>Job: {job.name}</h1>
        {getStatusBadge(liveStatus ?? job.status)}
        <div
          style={{
            marginLeft: "auto",
            display: "inline-flex",
            flexDirection: "column",
            alignItems: "flex-end",
            gap: "0.25rem",
          }}
        >
          <div
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "0.5rem",
            }}
          >
            <button
              className="btn btn-primary"
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "0.5rem",
              }}
              onClick={handleDownloadOutput}
              disabled={downloading}
            >
              {downloading ? (
                <Loader2 size={18} className="animate-spin" />
              ) : (
                <Download size={18} />
              )}
              {!downloading
                ? "Download Output"
                : downloadProgress?.phase === "downloading"
                  ? `Downloading… ${formatBytes(downloadProgress.loadedBytes)}${downloadProgress.totalBytes != null ? ` of ${formatBytes(downloadProgress.totalBytes)}` : ""}`
                  : "Preparing…"}
            </button>
            {downloading && (
              <button
                className="btn btn-secondary"
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "0.25rem",
                }}
                onClick={handleCancelDownload}
                title="Cancel download"
              >
                <X size={16} />
                Cancel
              </button>
            )}
          </div>
          {downloading && downloadProgress?.totalBytes != null && (
            <progress
              value={downloadProgress.loadedBytes}
              max={downloadProgress.totalBytes}
              style={{ width: "100%", height: "6px" }}
              aria-label="Download progress"
            />
          )}
          {downloading &&
            downloadProgress?.phase === "downloading" &&
            downloadProgress.totalBytes == null &&
            downloadProgress.loadedBytes > 0 && (
              <span style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>
                {formatBytes(downloadProgress.loadedBytes)} received…
              </span>
            )}
          {downloadError && (
            <span
              role="alert"
              style={{ color: "var(--danger, #ef4444)", fontSize: "0.8rem" }}
            >
              {downloadError}
            </span>
          )}
        </div>
      </div>

      {job.status === "ImageReady" && (job.trainingEligible === false || job.sourceKind === 'PACKAGES_ONLY') && (
        <div className="card ws-next-step" style={{ marginBottom: "1.5rem" }}>
          <MonitorPlay size={18} />
          <div>
            <strong>Interactive only · no workspace files</strong>
            <p>
              This image has no workspace files and is interactive-only. Open it as an{" "}
              <Link to={`/interactive/new?job=${encodeURIComponent(job.id)}`}>interactive workspace</Link>,
              add and save files, then submit the saved workspace for training.
            </p>
          </div>
        </div>
      )}

      {job.status === "ImageReady" && job.trainingEligible !== false && job.sourceKind !== 'PACKAGES_ONLY' && (
        <div className="card ws-next-step" style={{ marginBottom: "1.5rem" }}>
          <Rocket size={18} />
          <div>
            <strong>Image ready — no training command yet</strong>
            <p>
              This workspace was built without an entry command. Provide the entry and
              resume commands on the <Link to={`/training?job=${encodeURIComponent(job.id)}`}>Training page</Link>{" "}
              to run VRAM estimation and start training.
            </p>
          </div>
        </div>
      )}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "300px 1fr",
          gap: "2rem",
          flex: 1,
          minHeight: 0,
        }}
      >
        {/* Job Details Sidebar */}
        <div className="card" style={{ height: "fit-content" }}>
          <h3>Details</h3>
          <p
            style={{
              color: "var(--text-secondary)",
              fontSize: "0.9rem",
              marginTop: "0.5rem",
            }}
          >
            Download a zip archive with the submitted and generated training
            files.
          </p>
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: "1rem",
              marginTop: "1rem",
            }}
          >
            <div>
              <div
                style={{
                  color: "var(--text-secondary)",
                  fontSize: "0.75rem",
                  textTransform: "uppercase",
                }}
              >
                ID
              </div>
              <div className="build-detail-id">
                <span style={{ fontFamily: "monospace", wordBreak: "break-all" }}>{job.id}</span>
                <CopyButton value={job.id} title={`Copy job id ${job.id}`} />
              </div>
            </div>
            <div>
              <div
                style={{
                  color: "var(--text-secondary)",
                  fontSize: "0.75rem",
                  textTransform: "uppercase",
                }}
              >
                PyTorch Version
              </div>
              <div>{job.pytorchVersion}</div>
            </div>
            <div>
              <div
                style={{
                  color: "var(--text-secondary)",
                  fontSize: "0.75rem",
                  textTransform: "uppercase",
                }}
              >
                CUDA Version
              </div>
              <div>{job.cudaVersion}</div>
            </div>
            <div>
              <div
                style={{
                  color: "var(--text-secondary)",
                  fontSize: "0.75rem",
                  textTransform: "uppercase",
                }}
              >
                Submitted At
              </div>
              <div>{new Date(job.submittedAt).toLocaleString()}</div>
            </div>
            <div>
              <div
                style={{
                  color: "var(--text-secondary)",
                  fontSize: "0.75rem",
                  textTransform: "uppercase",
                }}
              >
                GPU Hours
              </div>
              <div>{job.gpuHours.toFixed(2)}</div>
            </div>
            {(job.status === "Pending" || job.status === "Building") &&
              job.queuePosition !== undefined && (
                <div>
                  <div
                    style={{
                      color: "var(--text-secondary)",
                      fontSize: "0.75rem",
                      textTransform: "uppercase",
                    }}
                  >
                    Queue Position
                  </div>
                  <div
                    style={{
                      color: "var(--accent-primary)",
                      fontWeight: "bold",
                    }}
                  >
                    #{job.queuePosition}
                  </div>
                </div>
              )}
            {job.packages && (
              <div>
                <div
                  style={{
                    color: "var(--text-secondary)",
                    fontSize: "0.75rem",
                    textTransform: "uppercase",
                  }}
                >
                  Packages
                </div>
                <div style={{ fontSize: "0.85rem", wordBreak: "break-word" }}>{job.packages}</div>
              </div>
            )}
            <Link
              to={`/builds/${job.id}`}
              className="btn btn-secondary"
              style={{ textDecoration: "none", justifyContent: "center" }}
            >
              View image build log
            </Link>
          </div>
        </div>

        {/* Terminal Window */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            minHeight: "500px",
          }}
        >
          <h3 style={{ marginBottom: "1rem" }}>Training logs</h3>
          <LogTerminal logs={logs} jobId={job.id} title={`training — job ${job.id}`} />
        </div>
      </div>
    </div>
  );
};

export default JobDetails;
