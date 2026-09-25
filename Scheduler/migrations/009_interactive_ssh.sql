-- VS Code Remote-SSH (plan.md §4-§6). Additive; existing rows stay valid.
ALTER TABLE interactive_runtimes ADD COLUMN IF NOT EXISTS ssh_capable BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE interactive_runtimes ADD COLUMN IF NOT EXISTS ssh_ready BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE interactive_runtimes ADD COLUMN IF NOT EXISTS ssh_status VARCHAR NOT NULL DEFAULT 'disabled';
ALTER TABLE interactive_runtimes ADD COLUMN IF NOT EXISTS ssh_host_key VARCHAR;
ALTER TABLE interactive_runtimes ADD COLUMN IF NOT EXISTS ssh_key_fingerprint VARCHAR;
ALTER TABLE interactive_runtimes ADD COLUMN IF NOT EXISTS ssh_generation INTEGER;
ALTER TABLE interactive_runtimes ADD COLUMN IF NOT EXISTS ssh_connection_requested_at TIMESTAMPTZ;
-- Scoped CLI refresh tokens (opaque, server-side hash/revocation, finite expiry).
CREATE TABLE IF NOT EXISTS cli_refresh_tokens (
    id VARCHAR PRIMARY KEY,
    user_id VARCHAR NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    scope VARCHAR(64) NOT NULL DEFAULT 'interactive:ssh',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    replaced_by VARCHAR
);
CREATE INDEX IF NOT EXISTS ix_cli_refresh_tokens_user ON cli_refresh_tokens (user_id);
ALTER TABLE interactive_image_revisions ADD COLUMN IF NOT EXISTS ssh_profile VARCHAR;
