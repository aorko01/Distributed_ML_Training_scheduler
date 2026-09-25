-- Burst-tolerant SSH grants (VS Code opens install + exec legs in parallel).
-- Additive; a small fixed window preserves abuse control (still N/minute).
ALTER TABLE interactive_runtimes ADD COLUMN IF NOT EXISTS ssh_grant_window_start TIMESTAMPTZ;
ALTER TABLE interactive_runtimes ADD COLUMN IF NOT EXISTS ssh_grant_window_count INTEGER NOT NULL DEFAULT 0;
