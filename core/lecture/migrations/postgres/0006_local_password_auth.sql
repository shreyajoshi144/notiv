-- Local email/password authentication, revocable sessions and one-time password resets.
--@add_column users password_hash TEXT
CREATE TABLE IF NOT EXISTS auth_sessions(
  id TEXT PRIMARY KEY, user_id BIGINT NOT NULL, expires_at DOUBLE PRECISION NOT NULL, created_at DOUBLE PRECISION NOT NULL,
  revoked_at DOUBLE PRECISION, csrf_hash TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_auth_sessions_user ON auth_sessions(user_id, expires_at);
CREATE TABLE IF NOT EXISTS password_resets(
  token_hash TEXT PRIMARY KEY, user_id BIGINT NOT NULL, expires_at DOUBLE PRECISION NOT NULL, used_at DOUBLE PRECISION, created_at DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS ix_password_resets_user ON password_resets(user_id, expires_at);
CREATE UNIQUE INDEX IF NOT EXISTS ux_users_email_lower ON users(lower(email)) WHERE password_hash IS NOT NULL;
