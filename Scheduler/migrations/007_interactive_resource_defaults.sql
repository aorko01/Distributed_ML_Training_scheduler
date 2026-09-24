-- Additive, rerunnable migration: persisted workspace default requirements.
--
-- Adds an optional JSON column to interactive_workspaces. Existing rows stay
-- NULL and fall back to operator defaults. Does not touch runtime or
-- assignment constraints/triggers.

ALTER TABLE interactive_workspaces
  ADD COLUMN IF NOT EXISTS default_resource_requirements JSON;
