-- Additive, idempotent PostgreSQL migration. Execute before starting upgraded API.
-- DB-backed worker credentials managed via the Admin UI (no restart needed).
-- worker_auth checks this table first, then falls back to the JSON file.
CREATE TABLE IF NOT EXISTS worker_credentials (
  worker_id VARCHAR PRIMARY KEY,
  secrets JSON NOT NULL DEFAULT '[]',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
