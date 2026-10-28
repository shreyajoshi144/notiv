-- 0003 Personal notes (PostgreSQL): private to the author, anchored to a lecture, optionally linked to evidence.
CREATE TABLE IF NOT EXISTS user_notes(
  id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, lecture_id BIGINT NOT NULL, workspace_id BIGINT NOT NULL,
  body TEXT NOT NULL, slide_index INTEGER, t DOUBLE PRECISION, created_at DOUBLE PRECISION NOT NULL, updated_at DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS ix_notes_user_lecture ON user_notes(user_id, lecture_id, id);
CREATE INDEX IF NOT EXISTS ix_notes_lecture ON user_notes(lecture_id);
CREATE TABLE IF NOT EXISTS user_note_links(
  note_id BIGINT NOT NULL, evidence_id BIGINT NOT NULL, quote TEXT, slide_index INTEGER, start DOUBLE PRECISION,
  PRIMARY KEY(note_id, evidence_id));
CREATE INDEX IF NOT EXISTS ix_note_links_ev ON user_note_links(evidence_id);
