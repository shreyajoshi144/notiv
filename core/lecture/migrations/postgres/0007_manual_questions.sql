-- Personal manual questions are separate from generated, evidence-grounded study items.
CREATE TABLE IF NOT EXISTS manual_questions(
  id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL,
  lecture_id BIGINT NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
  question TEXT NOT NULL, options TEXT NOT NULL,
  correct_index INTEGER NOT NULL CHECK(correct_index BETWEEN 0 AND 3),
  explanation TEXT NOT NULL DEFAULT '', updated_at DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS ix_manual_questions_owner ON manual_questions(user_id,lecture_id,id);
