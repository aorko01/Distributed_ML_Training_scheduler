import { useEffect, useRef } from 'react';
export function Dialog({ title, children, onClose, onConfirm, confirmLabel = 'Confirm', danger = false }: { title: string; children: React.ReactNode; onClose: () => void; onConfirm?: () => void; confirmLabel?: string; danger?: boolean }) {
  const confirmRef = useRef<HTMLButtonElement>(null);
  const prevFocus = useRef<HTMLElement | null>(null);
  useEffect(() => { prevFocus.current = document.activeElement as HTMLElement | null; confirmRef.current?.focus(); const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); }; window.addEventListener('keydown', h); return () => { window.removeEventListener('keydown', h); try { prevFocus.current?.focus(); } catch { /* ignore */ } }; }, [onClose]);
  return (
    <div className="ide-dialog-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="ide-dialog" role="dialog" aria-modal="true" aria-label={title}>
        <h2>{title}</h2>
        <div className="ide-dialog-body">{children}</div>
        <div className="ide-dialog-actions">
          <button type="button" className="ide-btn" onClick={onClose}>Cancel</button>
          {onConfirm && <button type="button" ref={confirmRef} className={danger ? 'ide-btn ide-btn-danger' : 'ide-btn ide-btn-primary'} onClick={onConfirm}>{confirmLabel}</button>}
        </div>
      </div>
    </div>
  );
}
