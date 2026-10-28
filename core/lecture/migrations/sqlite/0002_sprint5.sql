-- 0002 Sprint 5 production hardening (SQLite).
--@add_column jobs worker_id TEXT
--@add_column jobs error_class TEXT
-- Collapse historical duplicate active jobs (keep the oldest) so the uniqueness guard below can be built.
UPDATE jobs SET status='cancelled', finished_at=COALESCE(finished_at, strftime('%s','now')), error=COALESCE(error,'duplicate active job collapsed by migration 0002')
  WHERE status IN ('queued','running','retrying')
    AND id NOT IN (SELECT MIN(id) FROM jobs WHERE status IN ('queued','running','retrying') GROUP BY lecture_id, type);
-- At most ONE active job per (lecture, type): enqueue is idempotent and concurrent enqueues cannot double-run.
CREATE UNIQUE INDEX IF NOT EXISTS ux_jobs_active ON jobs(lecture_id, type) WHERE status IN ('queued','running','retrying');
CREATE INDEX IF NOT EXISTS ix_jobs_lecture ON jobs(lecture_id);
CREATE INDEX IF NOT EXISTS ix_lectures_ws ON lectures(workspace_id);
CREATE INDEX IF NOT EXISTS ix_lectures_course ON lectures(course_id);
CREATE INDEX IF NOT EXISTS ix_lectures_owner ON lectures(owner_id);
CREATE INDEX IF NOT EXISTS ix_courses_ws ON courses(workspace_id);
CREATE INDEX IF NOT EXISTS ix_members_user ON workspace_members(user_id);
CREATE INDEX IF NOT EXISTS ix_assets_ws ON assets(workspace_id);
CREATE INDEX IF NOT EXISTS ix_assets_uploader ON assets(uploader_id, created_at);
CREATE INDEX IF NOT EXISTS ix_ev_lecture ON lecture_evidence(lecture_id);
CREATE INDEX IF NOT EXISTS ix_slides_lecture ON lecture_slides(lecture_id);
CREATE INDEX IF NOT EXISTS ix_chat_user ON chat_conversations(user_id);
CREATE INDEX IF NOT EXISTS ix_study_sessions_user ON study_sessions(user_id, scope_type, scope_id);
CREATE INDEX IF NOT EXISTS ix_attempts_item ON study_attempts(item_id);
CREATE INDEX IF NOT EXISTS ix_progress_item ON study_progress(item_id);
CREATE INDEX IF NOT EXISTS ix_know_ev ON knowledge_evidence(evidence_id);
CREATE INDEX IF NOT EXISTS ix_study_ev ON study_item_evidence(evidence_id);
CREATE TABLE IF NOT EXISTS idempotency_keys(
  user_id INTEGER NOT NULL, scope TEXT NOT NULL, key TEXT NOT NULL, request_hash TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'in_progress', response_status INTEGER, response_body TEXT, created_at REAL NOT NULL,
  PRIMARY KEY(user_id, scope, key));
CREATE INDEX IF NOT EXISTS ix_idem_created ON idempotency_keys(created_at);
CREATE TABLE IF NOT EXISTS rate_limits(
  bucket TEXT NOT NULL, window_start INTEGER NOT NULL, count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(bucket, window_start));
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, user_id INTEGER, action TEXT NOT NULL,
  resource_type TEXT, resource_id INTEGER, workspace_id INTEGER, request_id TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS ix_audit_user ON audit_log(user_id, ts);
CREATE INDEX IF NOT EXISTS ix_audit_res ON audit_log(resource_type, resource_id);
-- Outbox: object deletions recorded in the SAME transaction as the row deletes, executed afterwards (retried until done).
CREATE TABLE IF NOT EXISTS storage_deletions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, target TEXT NOT NULL, created_at REAL NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, done_at REAL);
CREATE INDEX IF NOT EXISTS ix_stor_del_pending ON storage_deletions(done_at, id);
CREATE TABLE IF NOT EXISTS worker_heartbeats(
  worker_id TEXT PRIMARY KEY, beat_at REAL NOT NULL, started_at REAL NOT NULL, info TEXT);
