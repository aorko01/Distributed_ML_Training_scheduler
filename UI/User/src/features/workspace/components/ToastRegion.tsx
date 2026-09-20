import type { Notice } from '../workspaceTypes';
export function ToastRegion({ notices, onDismiss }: { notices: Notice[]; onDismiss: (id: string) => void }) {
  return (
    <div className="ide-toasts" aria-live="polite">
      {notices.map((n) => (
        <div key={n.id} role="status" className={`ide-toast ide-toast-${n.kind}`}>
          <span>{n.text}</span>
          <button type="button" aria-label="Dismiss notification" title="Dismiss" onClick={() => onDismiss(n.id)}>×</button>
        </div>
      ))}
    </div>
  );
}
