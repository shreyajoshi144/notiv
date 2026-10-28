-- 0001 baseline (PostgreSQL 13+). Equivalent to the SQLite baseline (+ additive columns folded in).
-- Differences: BIGSERIAL ids, DOUBLE PRECISION times, BYTEA vectors, "end" quoted (reserved word), and the two FTS5
-- virtual tables become ordinary tables with a generated tsvector + GIN index (same DELETE/INSERT interface).
CREATE TABLE IF NOT EXISTS workspaces(
  id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, owner_id BIGINT NOT NULL, created_at DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS workspace_members(
  workspace_id BIGINT NOT NULL, user_id BIGINT NOT NULL, role TEXT NOT NULL DEFAULT 'member',
  PRIMARY KEY(workspace_id, user_id));
CREATE TABLE IF NOT EXISTS courses(
  id BIGSERIAL PRIMARY KEY, owner_id BIGINT NOT NULL, name TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL,
  workspace_id BIGINT);
CREATE TABLE IF NOT EXISTS lectures(
  id BIGSERIAL PRIMARY KEY, owner_id BIGINT NOT NULL, title TEXT,
  status TEXT NOT NULL DEFAULT 'queued', stage TEXT, progress_pct INTEGER DEFAULT 0,
  error TEXT, video_path TEXT, deck_path TEXT, language TEXT, duration DOUBLE PRECISION,
  created_at DOUBLE PRECISION NOT NULL, workspace_id BIGINT, course_id BIGINT);
CREATE TABLE IF NOT EXISTS assets(
  id BIGSERIAL PRIMARY KEY, lecture_id BIGINT NOT NULL, workspace_id BIGINT NOT NULL,
  uploader_id BIGINT, type TEXT NOT NULL,
  storage_key TEXT NOT NULL, original_filename TEXT, mime_type TEXT, size_bytes BIGINT,
  checksum TEXT, duration_seconds DOUBLE PRECISION, status TEXT NOT NULL DEFAULT 'pending',
  created_at DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS ix_assets ON assets(lecture_id, type);
CREATE TABLE IF NOT EXISTS lecture_slides(
  lecture_id BIGINT NOT NULL, idx INTEGER NOT NULL, image_path TEXT, text TEXT,
  id BIGINT, image_key TEXT,
  PRIMARY KEY(lecture_id, idx));
CREATE TABLE IF NOT EXISTS lecture_segments(
  id BIGSERIAL PRIMARY KEY, lecture_id BIGINT NOT NULL, start DOUBLE PRECISION, "end" DOUBLE PRECISION,
  text TEXT, slide_index INTEGER, straddles INTEGER DEFAULT 0,
  confidence DOUBLE PRECISION, manual_slide INTEGER DEFAULT 0, edited INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_seg ON lecture_segments(lecture_id, start);
CREATE TABLE IF NOT EXISTS lecture_timeline(
  lecture_id BIGINT NOT NULL, slide_index INTEGER, start DOUBLE PRECISION, "end" DOUBLE PRECISION,
  confidence DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS lecture_evidence(
  id BIGSERIAL PRIMARY KEY, lecture_id BIGINT NOT NULL,
  source_type TEXT NOT NULL,
  source_id BIGINT NOT NULL,
  start DOUBLE PRECISION, "end" DOUBLE PRECISION, slide_index INTEGER, text TEXT NOT NULL, confidence DOUBLE PRECISION,
  created_at DOUBLE PRECISION NOT NULL, slide_id BIGINT, source_asset_id BIGINT);
CREATE INDEX IF NOT EXISTS ix_ev ON lecture_evidence(lecture_id, source_type, source_id);
CREATE TABLE IF NOT EXISTS evidence_fts(
  text TEXT NOT NULL, evidence_id BIGINT NOT NULL, lecture_id BIGINT NOT NULL,
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('english'::regconfig, text)) STORED);
CREATE INDEX IF NOT EXISTS ix_evidence_fts_tsv ON evidence_fts USING GIN(tsv);
CREATE INDEX IF NOT EXISTS ix_evidence_fts_lecture ON evidence_fts(lecture_id);
CREATE INDEX IF NOT EXISTS ix_evidence_fts_evidence ON evidence_fts(evidence_id);
CREATE TABLE IF NOT EXISTS jobs(
  id BIGSERIAL PRIMARY KEY, lecture_id BIGINT NOT NULL, workspace_id BIGINT NOT NULL,
  type TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
  attempt INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3,
  progress INTEGER NOT NULL DEFAULT 0, error TEXT, run_after DOUBLE PRECISION NOT NULL DEFAULT 0, heartbeat_at DOUBLE PRECISION,
  started_at DOUBLE PRECISION, finished_at DOUBLE PRECISION, created_at DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS ix_jobs ON jobs(status, run_after);
CREATE TABLE IF NOT EXISTS lecture_insights(
  id BIGSERIAL PRIMARY KEY, lecture_id BIGINT NOT NULL,
  kind TEXT NOT NULL,
  title TEXT NOT NULL, body TEXT, reason TEXT, method TEXT NOT NULL,
  start DOUBLE PRECISION, "end" DOUBLE PRECISION, slide_index INTEGER, position INTEGER NOT NULL DEFAULT 0, created_at DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS ix_insights ON lecture_insights(lecture_id, kind, position);
CREATE TABLE IF NOT EXISTS insight_evidence(
  insight_id BIGINT NOT NULL, evidence_id BIGINT NOT NULL, position INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(insight_id, evidence_id));
CREATE TABLE IF NOT EXISTS lecture_insight_meta(
  lecture_id BIGINT PRIMARY KEY, fingerprint TEXT NOT NULL, generated_at DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS evidence_embeddings(
  evidence_id BIGINT PRIMARY KEY, lecture_id BIGINT NOT NULL, vec BYTEA NOT NULL);
CREATE INDEX IF NOT EXISTS ix_embed ON evidence_embeddings(lecture_id);
CREATE TABLE IF NOT EXISTS lecture_embed_meta(
  lecture_id BIGINT PRIMARY KEY, model TEXT NOT NULL, dim INTEGER NOT NULL, fingerprint TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS chat_conversations(
  id BIGSERIAL PRIMARY KEY, lecture_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
  created_at DOUBLE PRECISION NOT NULL, updated_at DOUBLE PRECISION NOT NULL, UNIQUE(lecture_id, user_id));
CREATE TABLE IF NOT EXISTS chat_messages(
  id BIGSERIAL PRIMARY KEY, conversation_id BIGINT NOT NULL, role TEXT NOT NULL,
  content TEXT NOT NULL, grounded INTEGER, meta TEXT, created_at DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS ix_chat ON chat_messages(conversation_id, id);
CREATE TABLE IF NOT EXISTS chat_message_evidence(
  message_id BIGINT NOT NULL, evidence_id BIGINT NOT NULL, position INTEGER NOT NULL, snapshot TEXT,
  PRIMARY KEY(message_id, evidence_id));
CREATE TABLE IF NOT EXISTS knowledge_items(
  id BIGSERIAL PRIMARY KEY, lecture_id BIGINT NOT NULL,
  kind TEXT NOT NULL,
  title TEXT NOT NULL, body TEXT, concept_key TEXT, slide_index INTEGER, start DOUBLE PRECISION, "end" DOUBLE PRECISION,
  method TEXT NOT NULL, confidence DOUBLE PRECISION, dedupe_key TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL,
  UNIQUE(lecture_id, dedupe_key));
CREATE INDEX IF NOT EXISTS ix_know ON knowledge_items(lecture_id, kind);
CREATE TABLE IF NOT EXISTS knowledge_evidence(
  item_id BIGINT NOT NULL, evidence_id BIGINT NOT NULL, position INTEGER NOT NULL, role TEXT NOT NULL DEFAULT 'support',
  quote TEXT, PRIMARY KEY(item_id, evidence_id));
CREATE TABLE IF NOT EXISTS knowledge_fts(
  title TEXT, body TEXT, item_id BIGINT NOT NULL, lecture_id BIGINT NOT NULL,
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('english'::regconfig, COALESCE(title, '') || ' ' || COALESCE(body, ''))) STORED);
CREATE INDEX IF NOT EXISTS ix_knowledge_fts_tsv ON knowledge_fts USING GIN(tsv);
CREATE INDEX IF NOT EXISTS ix_knowledge_fts_lecture ON knowledge_fts(lecture_id);
CREATE TABLE IF NOT EXISTS knowledge_tombstones(lecture_id BIGINT NOT NULL, dedupe_key TEXT NOT NULL, PRIMARY KEY(lecture_id, dedupe_key));
CREATE TABLE IF NOT EXISTS knowledge_meta(lecture_id BIGINT PRIMARY KEY, fingerprint TEXT NOT NULL, generated_at DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS knowledge_reviews(
  user_id BIGINT NOT NULL, knowledge_id BIGINT NOT NULL, count INTEGER NOT NULL DEFAULT 1, reviewed_at DOUBLE PRECISION NOT NULL,
  PRIMARY KEY(user_id, knowledge_id));
CREATE TABLE IF NOT EXISTS study_items(
  id BIGSERIAL PRIMARY KEY, lecture_id BIGINT NOT NULL,
  kind TEXT NOT NULL,
  subtype TEXT NOT NULL, difficulty TEXT NOT NULL, prompt TEXT NOT NULL, answer TEXT NOT NULL, explanation TEXT,
  options TEXT, correct_option TEXT, key_points TEXT, knowledge_id BIGINT, concept_key TEXT, slide_index INTEGER,
  origin TEXT NOT NULL DEFAULT 'generated',
  status TEXT NOT NULL DEFAULT 'active',
  dedupe_key TEXT NOT NULL, regen_count INTEGER NOT NULL DEFAULT 0, created_at DOUBLE PRECISION NOT NULL, updated_at DOUBLE PRECISION NOT NULL,
  UNIQUE(lecture_id, dedupe_key));
CREATE INDEX IF NOT EXISTS ix_study ON study_items(lecture_id, status, kind);
CREATE TABLE IF NOT EXISTS study_item_evidence(
  item_id BIGINT NOT NULL, evidence_id BIGINT NOT NULL, position INTEGER NOT NULL, role TEXT NOT NULL DEFAULT 'answer',
  quote TEXT, PRIMARY KEY(item_id, evidence_id));
CREATE TABLE IF NOT EXISTS study_meta(lecture_id BIGINT PRIMARY KEY, fingerprint TEXT NOT NULL, generated_at DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS study_sessions(
  id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, scope_type TEXT NOT NULL,
  scope_id BIGINT NOT NULL, config TEXT, created_at DOUBLE PRECISION NOT NULL, finished_at DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS study_session_items(session_id BIGINT NOT NULL, item_id BIGINT NOT NULL, position INTEGER NOT NULL,
  PRIMARY KEY(session_id, item_id));
CREATE TABLE IF NOT EXISTS study_attempts(
  id BIGSERIAL PRIMARY KEY, session_id BIGINT NOT NULL, item_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
  correct INTEGER NOT NULL, self_grade TEXT, selected_option TEXT, response_text TEXT, created_at DOUBLE PRECISION NOT NULL,
  UNIQUE(session_id, item_id));
CREATE TABLE IF NOT EXISTS study_progress(
  user_id BIGINT NOT NULL, item_id BIGINT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, correct_count INTEGER NOT NULL DEFAULT 0,
  streak INTEGER NOT NULL DEFAULT 0, last_correct INTEGER, last_attempt_at DOUBLE PRECISION, PRIMARY KEY(user_id, item_id));
CREATE TABLE IF NOT EXISTS job_stages(
  job_id BIGINT NOT NULL, stage TEXT NOT NULL, status TEXT NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 0, error TEXT, output TEXT, started_at DOUBLE PRECISION, finished_at DOUBLE PRECISION,
  seq BIGSERIAL,
  PRIMARY KEY(job_id, stage));
