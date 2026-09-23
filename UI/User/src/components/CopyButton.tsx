import React, { useEffect, useRef, useState } from 'react';
import { Check, Copy } from 'lucide-react';

interface CopyButtonProps {
  /** Text written to the clipboard. */
  value: string;
  /** Visible label; omit for an icon-only button. */
  label?: string;
  title?: string;
  className?: string;
  /** Called with the copy result so callers can announce failures. */
  onResult?: (ok: boolean) => void;
}

/**
 * Copies a value (typically a job id) to the clipboard and briefly confirms it.
 * Falls back to a hidden textarea when the async Clipboard API is unavailable
 * (e.g. an insecure origin).
 */
const CopyButton: React.FC<CopyButtonProps> = ({ value, label, title, className, onResult }) => {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  const copy = async (event: React.MouseEvent) => {
    // Cards/rows around this button navigate on click.
    event.stopPropagation();
    const ok = await writeClipboard(value);
    onResult?.(ok);
    if (!ok) return;
    setCopied(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 1800);
  };

  return (
    <button
      type="button"
      className={className ?? 'btn btn-secondary copy-button'}
      onClick={copy}
      title={title ?? 'Copy to clipboard'}
      aria-label={title ?? 'Copy to clipboard'}
    >
      {copied ? <Check size={14} /> : <Copy size={14} />}
      {label ? <span>{copied ? 'Copied' : label}</span> : null}
    </button>
  );
};

async function writeClipboard(value: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch {
    // Fall through to the legacy path below.
  }
  try {
    const area = document.createElement('textarea');
    area.value = value;
    area.setAttribute('readonly', 'true');
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(area);
    return ok;
  } catch {
    return false;
  }
}

export default CopyButton;
