import React, { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  fetchJobById,
  fetchJobLogs,
  streamJobLogs,
  type Job,
  type JobStatus,
  type LogLine,
} from "../services/jobs";
import LogTerminal from "../components/LogTerminal";
import { ArrowLeft, Download, Loader2 } from "lucide-react";

interface ZipEntry {
  path: string;
  content: string;
}

const buildDummyOutputEntries = (job: Job): ZipEntry[] => [
  {
    path: "submitted/train.py",
    content: `# Dummy submitted training script for ${job.name}\nprint('Starting training...')\n`,
  },
  {
    path: "submitted/config.json",
    content: JSON.stringify(
      {
        jobId: job.id,
        model: job.name,
        pytorchVersion: job.pytorchVersion,
        cudaVersion: job.cudaVersion,
      },
      null,
      2,
    ),
  },
  {
    path: "generated/metrics.json",
    content: JSON.stringify(
      {
        status: job.status,
        accuracy: 0.91,
        loss: 0.23,
        note: "Dummy training output generated for the UI preview.",
      },
      null,
      2,
    ),
  },
];

const crc32Table = (() => {
  const table = new Uint32Array(256);
  for (let i = 0; i < 256; i += 1) {
    let crc = i;
    for (let j = 0; j < 8; j += 1) {
      crc = crc & 1 ? 0xedb88320 ^ (crc >>> 1) : crc >>> 1;
    }
    table[i] = crc >>> 0;
  }
  return table;
})();

const utf8Bytes = new TextEncoder();

const crc32 = (bytes: Uint8Array): number => {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc = crc32Table[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
};

const writeUint16LE = (view: DataView, offset: number, value: number) => {
  view.setUint16(offset, value, true);
};

const writeUint32LE = (view: DataView, offset: number, value: number) => {
  view.setUint32(offset, value, true);
};

const toArrayBuffer = (chunk: Uint8Array) => {
  const buffer = new ArrayBuffer(chunk.byteLength);
  new Uint8Array(buffer).set(chunk);
  return buffer;
};

const createZipBlob = (entries: ZipEntry[]) => {
  const localFiles: Uint8Array[] = [];
  const centralDirectory: Uint8Array[] = [];
  let offset = 0;

  entries.forEach((entry) => {
    const nameBytes = utf8Bytes.encode(entry.path);
    const dataBytes = utf8Bytes.encode(entry.content);
    const checksum = crc32(dataBytes);

    const localHeader = new Uint8Array(
      30 + nameBytes.length + dataBytes.length,
    );
    const localView = new DataView(localHeader.buffer);
    writeUint32LE(localView, 0, 0x04034b50);
    writeUint16LE(localView, 4, 20);
    writeUint16LE(localView, 6, 0);
    writeUint16LE(localView, 8, 0);
    writeUint16LE(localView, 10, 0);
    writeUint16LE(localView, 12, 0);
    writeUint32LE(localView, 14, checksum);
    writeUint32LE(localView, 18, dataBytes.length);
    writeUint32LE(localView, 22, dataBytes.length);
    writeUint16LE(localView, 26, nameBytes.length);
    writeUint16LE(localView, 28, 0);
    localHeader.set(nameBytes, 30);
    localHeader.set(dataBytes, 30 + nameBytes.length);
    localFiles.push(localHeader);

    const centralHeader = new Uint8Array(46 + nameBytes.length);
    const centralView = new DataView(centralHeader.buffer);
    writeUint32LE(centralView, 0, 0x02014b50);
    writeUint16LE(centralView, 4, 20);
    writeUint16LE(centralView, 6, 20);
    writeUint16LE(centralView, 8, 0);
    writeUint16LE(centralView, 10, 0);
    writeUint16LE(centralView, 12, 0);
    writeUint16LE(centralView, 14, 0);
    writeUint32LE(centralView, 16, checksum);
    writeUint32LE(centralView, 20, dataBytes.length);
    writeUint32LE(centralView, 24, dataBytes.length);
    writeUint16LE(centralView, 28, nameBytes.length);
    writeUint16LE(centralView, 30, 0);
    writeUint16LE(centralView, 32, 0);
    writeUint16LE(centralView, 34, 0);
    writeUint16LE(centralView, 36, 0);
    writeUint32LE(centralView, 38, 0);
    writeUint32LE(centralView, 42, offset);
    centralHeader.set(nameBytes, 46);
    centralDirectory.push(centralHeader);

    offset += localHeader.length;
  });

  const centralDirectorySize = centralDirectory.reduce(
    (total, chunk) => total + chunk.length,
    0,
  );
  const endRecord = new Uint8Array(22);
  const endView = new DataView(endRecord.buffer);
  writeUint32LE(endView, 0, 0x06054b50);
  writeUint16LE(endView, 4, 0);
  writeUint16LE(endView, 6, 0);
  writeUint16LE(endView, 8, entries.length);
  writeUint16LE(endView, 10, entries.length);
  writeUint32LE(endView, 12, centralDirectorySize);
  writeUint32LE(endView, 16, offset);
  writeUint16LE(endView, 20, 0);

  const blobParts: BlobPart[] = [
    ...localFiles.map(toArrayBuffer),
    ...centralDirectory.map(toArrayBuffer),
    toArrayBuffer(endRecord),
  ];

  return new Blob(blobParts, { type: "application/zip" });
};

const JobDetails: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [job, setJob] = useState<Job | null>(null);
  const [loading, setLoading] = useState(true);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [liveStatus, setLiveStatus] = useState<JobStatus | undefined>(undefined);

  const handleDownloadOutput = () => {
    if (!job) return;

    const zipBlob = createZipBlob(buildDummyOutputEntries(job));
    const downloadUrl = URL.createObjectURL(zipBlob);
    const link = document.createElement("a");
    link.href = downloadUrl;
    link.download = `${job.name.replace(/\s+/g, "_").toLowerCase()}-output.zip`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(downloadUrl);
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

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "Pending":
        return <span className="badge badge-pending">Pending</span>;
      case "Building":
        return <span className="badge badge-building">Building</span>;
      case "Running":
        return <span className="badge badge-running">Running</span>;
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
        >
          <Download size={18} />
          Download Output
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
