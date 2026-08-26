import React, { useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';

interface TerminalLine {
  text: string;
  kind: 'cmd' | 'out' | 'err' | 'accent';
}

const BANNER: TerminalLine[] = [
  { text: 'Welcome to Ubuntu 22.04 LTS (GNU/Linux 5.15.0 x86_64)', kind: 'out' },
  { text: '', kind: 'out' },
  { text: ' * Documentation:  https://dml.cloud/docs/interactive', kind: 'out' },
  { text: 'Last login: just now from 10.42.0.7', kind: 'out' },
  { text: "Type 'help' to see available dummy commands.", kind: 'accent' },
];

const GPU_SMI = `+---------------------------------------------------------------------------------------+
| NVIDIA-SMI 535.129.03   Driver Version: 535.129.03   CUDA Version: 12.2               |
|-------------------------------+----------------------+----------------------+
| GPU  Name        Persistence-M| Bus-Id        Disp.A | Volatile Uncorr. ECC |
| Fan  Temp  Perf  Pwr:Usage/Cap|         Memory-Usage | GPU-Util  Compute M. |
|===============================+======================+======================|
|   0  NVIDIA A100 80GB     On  | 00000000:07:00.0 Off |                    0 |
| N/A   62C    P0    187W / 400W |  12672MiB /  81920MiB |     68%      Default |
+-------------------------------+----------------------+----------------------+`;

const respond = (raw: string): TerminalLine[] => {
  const input = raw.trim();
  const [cmd, ...args] = input.split(/\s+/);
  switch (cmd) {
    case '':
      return [];
    case 'help':
      return [
        { text: 'Dummy shell — available commands:', kind: 'accent' },
        { text: '  ls  pwd  whoami  uptime  python -V  pip list', kind: 'out' },
        { text: '  nvidia-smi  cat <file>  echo <msg>  clear', kind: 'out' },
      ];
    case 'ls':
      return [{ text: 'data  models  output  Welcome.ipynb  train.py  requirements.txt  README.md', kind: 'out' }];
    case 'pwd':
      return [{ text: '/workspace', kind: 'out' }];
    case 'whoami':
      return [{ text: 'researcher', kind: 'out' }];
    case 'uptime':
      return [{ text: ' up 42 min,  1 user,  load average: 3.14, 2.71, 1.61', kind: 'out' }];
    case 'echo':
      return [{ text: args.join(' '), kind: 'out' }];
    case 'python':
    case 'python3':
      if (args[0] === '-V' || args[0] === '--version') {
        return [{ text: 'Python 3.11.9', kind: 'out' }];
      }
      return [{ text: `Python 3.11.9 (main) [PyTorch 2.3.1 CUDA 12.1] — interactive REPL not wired in this preview`, kind: 'err' }];
    case 'pip':
      if (args[0] === 'list') {
        return [
          { text: 'Package            Version', kind: 'out' },
          { text: '------------------ -------', kind: 'out' },
          { text: 'numpy              1.26.4', kind: 'out' },
          { text: 'torch              2.3.1', kind: 'out' },
          { text: 'torchvision        0.18.1', kind: 'out' },
          { text: 'tqdm               4.66.4', kind: 'out' },
        ];
      }
      return [{ text: 'Usage: pip list (dummy)', kind: 'err' }];
    case 'nvidia-smi':
      return [{ text: GPU_SMI, kind: 'accent' }];
    case 'cat':
      if (args[0] === 'README.md') {
        return [
          { text: '# Workspace', kind: 'out' },
          { text: 'Interactive session spun up from a base job image.', kind: 'out' },
        ];
      }
      return [{ text: `cat: ${args[0] ?? ''}: No such file or directory`, kind: 'err' }];
    case 'clear':
      return ['__CLEAR__' as unknown as TerminalLine];
    default:
      return [{ text: `bash: ${cmd}: command not found`, kind: 'err' }];
  }
};

interface FakeTerminalProps {
  onClose: () => void;
}

const FakeTerminal: React.FC<FakeTerminalProps> = ({ onClose }) => {
  const [lines, setLines] = useState<TerminalLine[]>(BANNER);
  const [input, setInput] = useState('');
  const bodyRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [lines]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleSubmit = () => {
    const echoLine: TerminalLine = { text: `researcher@node-a100-04:/workspace$ ${input}`, kind: 'cmd' };
    const result = respond(input);
    if (result.length > 0 && (result[0] as unknown as string) === '__CLEAR__') {
      setLines([]);
      setInput('');
      return;
    }
    setLines((prev) => [...prev.slice(-300), echoLine, ...result]);
    setInput('');
  };

  const lineClass = (kind: TerminalLine['kind']) => {
    switch (kind) {
      case 'cmd': return 'nb-term-cmd';
      case 'err': return 'term-error';
      case 'accent': return 'term-success';
      default: return '';
    }
  };

  return (
    <div className="nb-terminal">
      <div className="terminal-header">
        <div className="mac-btns">
          <div className="mac-btn close" />
          <div className="mac-btn minimize" />
          <div className="mac-btn maximize" />
        </div>
        <div className="terminal-title">bash — node-a100-04 · /workspace</div>
        <button className="modal-close" onClick={onClose} aria-label="Close terminal">
          <X size={15} />
        </button>
      </div>
      <div className="nb-terminal-body" ref={bodyRef} onClick={() => inputRef.current?.focus()}>
        {lines.map((line, i) => (
          <div key={i} className={`term-line ${lineClass(line.kind)}`} style={{ whiteSpace: 'pre-wrap' }}>
            {line.text}
          </div>
        ))}
        <div className="nb-term-input-row">
          <span className="nb-term-prompt">researcher@node-a100-04:/workspace$</span>
          <input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSubmit()}
            spellCheck={false}
            autoComplete="off"
          />
        </div>
      </div>
    </div>
  );
};

export default FakeTerminal;
