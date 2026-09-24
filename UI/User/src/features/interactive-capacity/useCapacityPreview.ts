import { useEffect, useRef, useState } from 'react';
import { interactiveCapacity, type CapacityPreview, type ResourceRequirements } from '../../services/interactive';

export function useCapacityPreview(requirements: ResourceRequirements | null, enabled = true, debounceMs = 300) {
  const [preview, setPreview] = useState<CapacityPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const seq = useRef(0);
  const key = requirements ? JSON.stringify(requirements) : '';

  useEffect(() => {
    if (!enabled || !requirements) {
      setPreview(null);
      setLoading(false);
      return;
    }
    const snapshot: ResourceRequirements = JSON.parse(key);
    const id = ++seq.current;
    setLoading(true);
    setError('');
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const data = await interactiveCapacity.preview(snapshot, controller.signal);
        if (seq.current === id) {
          setPreview(data);
          setLoading(false);
        }
      } catch (err) {
        if (controller.signal.aborted || seq.current !== id) return;
        setError(err instanceof Error ? err.message : 'Capacity preview failed');
        setLoading(false);
      }
    }, debounceMs);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, enabled, debounceMs]);

  return { preview, loading, error };
}
