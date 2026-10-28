-- 0001 baseline (SQLite). Idempotent: IF NOT EXISTS + conditional add-column, so it is safe on a fresh database AND on a
-- database created by Sprint 1-4 code (which built the same schema inline).
CREATE TABLE IF NOT EXISTS workspaces(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, owner_id INTEGER NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS workspace_members(
  workspace_id INTEGER NOT NULL, user_id INTEGER NOT NULL, role TEXT NOT NULL DEFAULT 'member',
  PRIMARY KEY(workspace_id, user_id));
CREATE TABLE IF NOT EXISTS courses(
  id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER NOT NULL, name TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS lectures(
  id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER NOT NULL, title TEXT,
  status TEXT NOT NULL DEFAULT 'queued', stage TEXT, progress_pct INTEGER DEFAULT 0,
  error TEXT, video_path TEXT, deck_path TEXT, language TEXT, duration REAL,
  created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS assets(
  id INTEGER PRIMARY KEY AUTOINCREMENT, lecture_id INTEGER NOT NULL, workspace_id INTEGER NOT NULL,
  uploader_id INTEGER, type TEXT NOT NULL,               -- video|audio|pdf|pptx|image
  storage_key TEXT NOT NULL, original_filename TEXT, mime_type TEXT, size_bytes INTEGER,
  checksum TEXT, duration_seconds REAL, status TEXT NOT NULL DEFAULT 'pending',  -- pending|ready|failed
  created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_assets ON assets(lecture_id, type);
CREATE TABLE IF NOT EXISTS lecture_slides(
  lecture_id INTEGER NOT NULL, idx INTEGER NOT NULL, image_path TEXT, text TEXT,
  PRIMARY KEY(lecture_id, idx));
CREATE TABLE IF NOT EXISTS lecture_segments(
  id INTEGER PRIMARY KEY AUTOINCREMENT, lecture_id INTEGER NOT NULL, start REAL, end REAL,
  text TEXT, slide_index INTEGER, straddles INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_seg ON lecture_segments(lecture_id, start);
CREATE TABLE IF NOT EXISTS lecture_timeline(
  lecture_id INTEGER NOT NULL, slide_index INTEGER, start REAL, end REAL);
CREATE TABLE IF NOT EXISTS lecture_evidence(
  id INTEGER PRIMARY KEY AUTOINCREMENT, lecture_id INTEGER NOT NULL,
  source_type TEXT NOT NULL,            -- 'transcript' | 'slide'
  source_id INTEGER NOT NULL,           -- segment id, or slide idx
  start REAL, end REAL, slide_index INTEGER, text TEXT NOT NULL, confidence REAL,
  created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_ev ON lecture_evidence(lecture_id, source_type, source_id);
CREATE VIRTUAL TABLE IF NOT EXISTS evidence_fts USING fts5(
  text, evidence_id UNINDEXED, lecture_id UNINDEXED, tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, lecture_id INTEGER NOT NULL, workspace_id INTEGER NOT NULL,
  type TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
  attempt INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3,
  progress INTEGER NOT NULL DEFAULT 0, error TEXT, run_after REAL NOT NULL DEFAULT 0, heartbeat_at REAL,
  started_at REAL, finished_at REAL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_jobs ON jobs(status, run_after);
CREATE TABLE IF NOT EXISTS lecture_insights(
  id INTEGER PRIMARY KEY AUTOINCREMENT, lecture_id INTEGER NOT NULL,
  kind TEXT NOT NULL,                   -- 'key_moment' | 'summary_section'  (Notiv-generated, never professor speech)
  title TEXT NOT NULL, body TEXT, reason TEXT, method TEXT NOT NULL,
  start REAL, end REAL, slide_index INTEGER, position INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_insights ON lecture_insights(lecture_id, kind, position);
CREATE TABLE IF NOT EXISTS insight_evidence(
  insight_id INTEGER NOT NULL, evidence_id INTEGER NOT NULL, position INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(insight_id, evidence_id));
CREATE TABLE IF NOT EXISTS lecture_insight_meta(
  lecture_id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL, generated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS evidence_embeddings(
  evidence_id INTEGER PRIMARY KEY, lecture_id INTEGER NOT NULL, vec BLOB NOT NULL);
CREATE INDEX IF NOT EXISTS ix_embed ON evidence_embeddings(lecture_id);
CREATE TABLE IF NOT EXISTS lecture_embed_meta(
  lecture_id INTEGER PRIMARY KEY, model TEXT NOT NULL, dim INTEGER NOT NULL, fingerprint TEXT NOT NULL, created_at REAL NOT NULL);
-- Ask Notiv: one conversation per (lecture, user). Citations are LINKS to lecture_evidence (the single citation
-- system); `snapshot` is only a readable fallback if that evidence is later replaced by re-processing.
CREATE TABLE IF NOT EXISTS chat_conversations(
  id INTEGER PRIMARY KEY AUTOINCREMENT, lecture_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
  created_at REAL NOT NULL, updated_at REAL NOT NULL, UNIQUE(lecture_id, user_id));
CREATE TABLE IF NOT EXISTS chat_messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER NOT NULL, role TEXT NOT NULL,   -- 'user' | 'assistant'
  content TEXT NOT NULL, grounded INTEGER, meta TEXT, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_chat ON chat_messages(conversation_id, id);
CREATE TABLE IF NOT EXISTS chat_message_evidence(
  message_id INTEGER NOT NULL, evidence_id INTEGER NOT NULL, position INTEGER NOT NULL, snapshot TEXT,
  PRIMARY KEY(message_id, evidence_id));
-- Sprint 4: study intelligence. Knowledge/study items are DERIVED from lecture_evidence; every link carries the
-- verbatim `quote` it relies on (checked to be a substring of that evidence at write time).
CREATE TABLE IF NOT EXISTS knowledge_items(
  id INTEGER PRIMARY KEY AUTOINCREMENT, lecture_id INTEGER NOT NULL,
  kind TEXT NOT NULL,                  -- concept|definition|formula|fact|example|emphasis|warning
  title TEXT NOT NULL, body TEXT, concept_key TEXT, slide_index INTEGER, start REAL, end REAL,
  method TEXT NOT NULL, confidence REAL, dedupe_key TEXT NOT NULL, created_at REAL NOT NULL,
  UNIQUE(lecture_id, dedupe_key));
CREATE INDEX IF NOT EXISTS ix_know ON knowledge_items(lecture_id, kind);
CREATE TABLE IF NOT EXISTS knowledge_evidence(
  item_id INTEGER NOT NULL, evidence_id INTEGER NOT NULL, position INTEGER NOT NULL, role TEXT NOT NULL DEFAULT 'support',
  quote TEXT, PRIMARY KEY(item_id, evidence_id));
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
  title, body, item_id UNINDEXED, lecture_id UNINDEXED, tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS knowledge_tombstones(lecture_id INTEGER NOT NULL, dedupe_key TEXT NOT NULL, PRIMARY KEY(lecture_id, dedupe_key));
CREATE TABLE IF NOT EXISTS knowledge_meta(lecture_id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL, generated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS knowledge_reviews(
  user_id INTEGER NOT NULL, knowledge_id INTEGER NOT NULL, count INTEGER NOT NULL DEFAULT 1, reviewed_at REAL NOT NULL,
  PRIMARY KEY(user_id, knowledge_id));
CREATE TABLE IF NOT EXISTS study_items(
  id INTEGER PRIMARY KEY AUTOINCREMENT, lecture_id INTEGER NOT NULL,
  kind TEXT NOT NULL,                  -- flashcard|short_answer|mcq
  subtype TEXT NOT NULL, difficulty TEXT NOT NULL, prompt TEXT NOT NULL, answer TEXT NOT NULL, explanation TEXT,
  options TEXT, correct_option TEXT, key_points TEXT, knowledge_id INTEGER, concept_key TEXT, slide_index INTEGER,
  origin TEXT NOT NULL DEFAULT 'generated',   -- generated | edited
  status TEXT NOT NULL DEFAULT 'active',      -- active | deleted (tombstone: regeneration will not recreate it)
  dedupe_key TEXT NOT NULL, regen_count INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL,
  UNIQUE(lecture_id, dedupe_key));
CREATE INDEX IF NOT EXISTS ix_study ON study_items(lecture_id, status, kind);
CREATE TABLE IF NOT EXISTS study_item_evidence(
  item_id INTEGER NOT NULL, evidence_id INTEGER NOT NULL, position INTEGER NOT NULL, role TEXT NOT NULL DEFAULT 'answer',
  quote TEXT, PRIMARY KEY(item_id, evidence_id));
CREATE TABLE IF NOT EXISTS study_meta(lecture_id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL, generated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS study_sessions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, scope_type TEXT NOT NULL,   -- lecture | course
  scope_id INTEGER NOT NULL, config TEXT, created_at REAL NOT NULL, finished_at REAL);
CREATE TABLE IF NOT EXISTS study_session_items(session_id INTEGER NOT NULL, item_id INTEGER NOT NULL, position INTEGER NOT NULL,
  PRIMARY KEY(session_id, item_id));
CREATE TABLE IF NOT EXISTS study_attempts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL, item_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
  correct INTEGER NOT NULL, self_grade TEXT, selected_option TEXT, response_text TEXT, created_at REAL NOT NULL,
  UNIQUE(session_id, item_id));
CREATE TABLE IF NOT EXISTS study_progress(
  user_id INTEGER NOT NULL, item_id INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, correct_count INTEGER NOT NULL DEFAULT 0,
  streak INTEGER NOT NULL DEFAULT 0, last_correct INTEGER, last_attempt_at REAL, PRIMARY KEY(user_id, item_id));
CREATE TABLE IF NOT EXISTS job_stages(
  job_id INTEGER NOT NULL, stage TEXT NOT NULL, status TEXT NOT NULL,   -- running|completed|failed|skipped
  attempt INTEGER NOT NULL DEFAULT 0, error TEXT, output TEXT, started_at REAL, finished_at REAL,
  PRIMARY KEY(job_id, stage));
--@add_column courses workspace_id INTEGER
--@add_column lectures workspace_id INTEGER
--@add_column lectures course_id INTEGER
--@add_column lecture_slides id INTEGER
--@add_column lecture_slides image_key TEXT
--@add_column lecture_timeline confidence REAL
--@add_column lecture_segments confidence REAL
--@add_column lecture_segments manual_slide INTEGER DEFAULT 0
--@add_column lecture_segments edited INTEGER DEFAULT 0
--@add_column lecture_evidence slide_id INTEGER
--@add_column lecture_evidence source_asset_id INTEGER
