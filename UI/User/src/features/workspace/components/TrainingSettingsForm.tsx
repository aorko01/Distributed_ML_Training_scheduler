import type { TrainingSettings } from '../../../services/interactive';

export function TrainingSettingsForm({ value, onChange }: {
  value: TrainingSettings;
  onChange: (value: TrainingSettings) => void;
}) {
  return <div className="ide-training-settings">
    <label>Job name<input value={value.name} maxLength={120} onChange={(e) => onChange({ ...value, name: e.target.value })} /></label>
    <label>Command<input value={value.command} maxLength={1024} placeholder="python train.py" onChange={(e) => onChange({ ...value, command: e.target.value })} /></label>
    <label>Resume command (optional)<input value={value.resume_command ?? ''} maxLength={1024} onChange={(e) => onChange({ ...value, resume_command: e.target.value || null })} /></label>
    <label>Priority<select value={value.priority ?? 'NORMAL'} onChange={(e) => onChange({ ...value, priority: e.target.value as TrainingSettings['priority'] })}>
      <option value="NORMAL">Normal</option><option value="REQUESTED">Requested</option><option value="HIGH">High</option>
    </select></label>
    {value.priority !== 'NORMAL' && <label>Reason for priority<input value={value.reason_for_priority ?? ''} maxLength={1000} onChange={(e) => onChange({ ...value, reason_for_priority: e.target.value || null })} /></label>}
  </div>;
}
