-- Additive, rerunnable migration: prepared developer image profile.
--
-- Records which revisions carry the plan.md developer-profile v1 image
-- (sudo/venv/home). Existing rows stay NULL and remain eligible only for the
-- existing strict runtime mode. Does not touch runtime or assignment
-- constraints/triggers.

ALTER TABLE interactive_image_revisions
  ADD COLUMN IF NOT EXISTS developer_profile VARCHAR;
