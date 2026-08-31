import React, { useEffect, useRef, useState } from 'react';

interface LogLine {
  type: 'info' | 'warn' | 'error' | 'success';
  text: string;
  timestamp: string;
}

interface LogTerminalProps {
  logs: LogLine[];
  jobId: string;
}

const LogTerminal: React.FC<LogTerminalProps> = ({ logs, jobId }) => {
  const bodyRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = React.useState(true);
  const [isDownloading, setIsDownloading] = useState(false);

  useEffect(() => {
    if (bodyRef.current && autoScroll) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  }, [logs, autoScroll]);

  const handleScroll = () => {
    if (bodyRef.current) {
      const { scrollTop, scrollHeight, clientHeight } = bodyRef.current;
      const isAtBottom = scrollHeight - scrollTop - clientHeight < 10;
      setAutoScroll(isAtBottom);
    }
  };

  const getLogClass = (type: string) => {
    switch (type) {
      case 'info': return 'term-info';
      case 'warn': return 'term-warn';
      case 'error': return 'term-error';
      case 'success': return 'term-success';
      default: return 'term-info';
    }
  };

  const formatTime = (isoString: string) => {
    const d = new Date(isoString);
    return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}:${d.getSeconds().toString().padStart(2, '0')}`;
  };

  const handleDownload = async () => {
    if (!jobId || isDownloading) return;

    try {
      setIsDownloading(true);

      const token = localStorage.getItem('auth_token');
      const response = await fetch(`${API_BASE_URL}/jobs/download_output`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token && { 'Authorization': `Bearer ${token}` }),
        },
        body: JSON.stringify({ job_id: jobId }),
      });

      if (!response.ok) {
        const errorText = await response.text();
        console.error('Download failed:', errorText);
        return;
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const filename = `job-${jobId}-output.zip`;

      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(url);

    } catch (error) {
      console.error('Error downloading output:', error);
    } finally {
      setIsDownloading(false);
    }
  };

  return (
    <div className="terminal-window">
      <div className="terminal-header">
        <div className="mac-btns">
          <div className="mac-btn close"></div>
          <div className="mac-btn minimize"></div>
          <div className="mac-btn maximize"></div>
        </div>
        <div className="terminal-title">bash - job {jobId}</div>
        <div className="terminal-actions">
          <button
            onClick={handleDownload}
            disabled={isDownloading}
            className="download-button"
            title="Download Output"
          >
            {isDownloading ? 'Downloading...' : 'Download Output'}
          </button>
        </div>
      </div>
      <div className="terminal-body" ref={bodyRef} onScroll={handleScroll}>
        {logs.map((log, index) => (
          <div key={index} className="term-line">
            <span className="term-timestamp">[{formatTime(log.timestamp)}]</span>
            <span className={getLogClass(log.type)}>{log.text}</span>
          </div>
        ))}
        {logs.length === 0 && (
          <div className="term-line term-info">Waiting for logs...</div>
        )}
      </div>
    </div>
  );
};

const API_BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

export default LogTerminal;
