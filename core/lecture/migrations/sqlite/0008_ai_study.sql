-- AI study material (document summary, sections, concepts, relations, flashcards, quiz) generated from a lecture's
-- evidence by the configured answer model. One validated artifact per lecture; regenerated when the evidence changes.
CREATE TABLE IF NOT EXISTS lecture_ai_study(
  lecture_id BIGINT PRIMARY KEY REFERENCES lectures(id) ON DELETE CASCADE,
  fingerprint TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
  content TEXT NOT NULL, created_at REAL NOT NULL);
