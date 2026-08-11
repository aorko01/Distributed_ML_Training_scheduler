import React, { useEffect, useRef } from 'react';

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

  return (
    <div className="terminal-window">
      <div className="terminal-header">
        <div className="mac-btns">
          <div className="mac-btn close"></div>
          <div className="mac-btn minimize"></div>
          <div className="mac-btn maximize"></div>
        </div>
        <div className="terminal-title">bash - job {jobId}</div>
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

export default LogTerminal;
