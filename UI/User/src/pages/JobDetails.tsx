import React, { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  fetchJobById,
  fetchJobLogs,
  streamJobLogs,
  downloadJobOutput,
  type Job,
  type JobStatus,
  type LogLine,
} from "../services/jobs";
import LogTerminal from "../components/LogTerminal";
import { ArrowLeft, Download, Loader2 } from "lucide-react";

const JobDetails: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [job, setJob] = useState<Job | null>(null);
  const [loading, setLoading] = useState(true);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [liveStatus, setLiveStatus] = useState<JobStatus | undefined>(undefined);

  const [downloading, setDownloading] = useState(false);

  const handleDownloadOutput = async () => {
    if (!job || downloading) return;

    setDownloading(true);
    try {
      await downloadJobOutput(job.id, job.name);
    } catch (error) {
      console.error("Failed to download output:", error);
      alert("Failed to download output. Please try again.");
    } finally {
      setDownloading(false);
    }
  };

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
        // Finished jobs: logs come purely from the object store.
        const stored = await fetchJobLogs(id);
        if (!cancelled) setLogs(stored);
        return;
      }

      // Running jobs: show object-store logs first, then stream realtime logs.
      stopStream = streamJobLogs(id, {
        onLog: (line) => {
          if (!cancelled) setLogs((prev) => [...prev, line]);
        },
        onDone: (status) => {
          if (cancelled) return;
          if (status === "COMPLETED") setLiveStatus("Completed");
          else if (status === "FAILED") setLiveStatus("Failed");
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
        <Loader2 className="animate-spin" size={32} />
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

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "Pending":
        return <span className="badge badge-pending">Pending</span>;
      case "Building":
        return <span className="badge badge-building">Building</span>;
      case "Running":
        return <span className="badge badge-running">Running</span>;
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
        <button
          className="btn btn-primary"
          style={{
            marginLeft: "auto",
            display: "inline-flex",
            alignItems: "center",
            gap: "0.5rem",
          }}
          onClick={handleDownloadOutput}
          disabled={downloading}
        >
          {downloading ? <Loader2 size={18} className="animate-spin" /> : <Download size={18} />}
          {downloading ? "Downloading..." : "Download Output"}
        </button>
      </div>

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
              <div style={{ fontFamily: "monospace" }}>{job.id}</div>
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
          <h3 style={{ marginBottom: "1rem" }}>Live Logs</h3>
          <LogTerminal logs={logs} jobId={job.id} />
        </div>
      </div>
    </div>
  );
};

export default JobDetails;
