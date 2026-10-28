-- Persistent notebook-centered workspace. All content is user-owned; sources are validated against lecture ownership in service code.
CREATE TABLE IF NOT EXISTS notebooks (
 id BIGSERIAL PRIMARY KEY, owner_id BIGINT NOT NULL, workspace_id BIGINT REFERENCES workspaces(id) ON DELETE SET NULL, title TEXT NOT NULL,
 description TEXT NOT NULL DEFAULT '', subject TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
 created_at DOUBLE PRECISION NOT NULL, updated_at DOUBLE PRECISION NOT NULL, archived_at DOUBLE PRECISION, UNIQUE(id, owner_id));
CREATE INDEX IF NOT EXISTS ix_notebooks_owner_updated ON notebooks(owner_id, status, updated_at DESC);
CREATE TABLE IF NOT EXISTS notebook_sources (
 id BIGSERIAL PRIMARY KEY, notebook_id BIGINT NOT NULL, owner_id BIGINT NOT NULL,
 source_type TEXT NOT NULL, source_id BIGINT NOT NULL, label TEXT NOT NULL DEFAULT '', metadata_json TEXT NOT NULL DEFAULT '{}', created_at DOUBLE PRECISION NOT NULL,
 UNIQUE(notebook_id, source_type, source_id), FOREIGN KEY(notebook_id, owner_id) REFERENCES notebooks(id, owner_id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS ix_notebook_sources_owner ON notebook_sources(owner_id, notebook_id);
CREATE TABLE IF NOT EXISTS notebook_items (
 id BIGSERIAL PRIMARY KEY, notebook_id BIGINT NOT NULL, owner_id BIGINT NOT NULL,
 item_type TEXT NOT NULL, title TEXT NOT NULL, content_json TEXT NOT NULL DEFAULT '{}', provenance_json TEXT NOT NULL DEFAULT '[]',
 version INTEGER NOT NULL DEFAULT 1, created_at DOUBLE PRECISION NOT NULL, updated_at DOUBLE PRECISION NOT NULL, deleted_at DOUBLE PRECISION, FOREIGN KEY(notebook_id, owner_id) REFERENCES notebooks(id, owner_id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS ix_notebook_items_owner ON notebook_items(owner_id, notebook_id, item_type, updated_at DESC);
CREATE TABLE IF NOT EXISTS notebook_canvas_state (
 notebook_id BIGINT PRIMARY KEY, owner_id BIGINT NOT NULL, state_json TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}', version INTEGER NOT NULL DEFAULT 1, updated_at DOUBLE PRECISION NOT NULL, FOREIGN KEY(notebook_id, owner_id) REFERENCES notebooks(id, owner_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS notebook_shares (
 id BIGSERIAL PRIMARY KEY, notebook_id BIGINT NOT NULL, owner_id BIGINT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
 permission TEXT NOT NULL DEFAULT 'read', expires_at DOUBLE PRECISION, revoked_at DOUBLE PRECISION, created_at DOUBLE PRECISION NOT NULL, FOREIGN KEY(notebook_id, owner_id) REFERENCES notebooks(id, owner_id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS ix_notebook_shares_notebook ON notebook_shares(owner_id, notebook_id, revoked_at);
