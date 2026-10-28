-- 0003 Personal notes (SQLite): private to the author, anchored to a lecture, optionally linked to evidence.
CREATE TABLE IF NOT EXISTS user_notes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, lecture_id INTEGER NOT NULL, workspace_id INTEGER NOT NULL,
  body TEXT NOT NULL, slide_index INTEGER, t REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_notes_user_lecture ON user_notes(user_id, lecture_id, id);
CREATE INDEX IF NOT EXISTS ix_notes_lecture ON user_notes(lecture_id);
-- evidence_id has NO foreign key on purpose: re-processing a lecture replaces its evidence rows, and a note must survive that.
-- The quote/position snapshot lets the UI show "source replaced" instead of silently losing the link.
CREATE TABLE IF NOT EXISTS user_note_links(
  note_id INTEGER NOT NULL, evidence_id INTEGER NOT NULL, quote TEXT, slide_index INTEGER, start REAL,
  PRIMARY KEY(note_id, evidence_id));
CREATE INDEX IF NOT EXISTS ix_note_links_ev ON user_note_links(evidence_id);
