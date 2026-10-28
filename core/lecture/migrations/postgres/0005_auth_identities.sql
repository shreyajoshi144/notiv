-- 0005 Sign-in identities (PostgreSQL): maps an OpenID Connect (issuer, subject) to a local numeric user id.
-- OIDC-created users get ids >= 1000000000 so they can never collide with ids minted by an external token issuer.
CREATE TABLE IF NOT EXISTS users(
  id BIGINT PRIMARY KEY, email TEXT, display_name TEXT, created_at DOUBLE PRECISION NOT NULL, last_login_at DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS auth_identities(
  id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, issuer TEXT NOT NULL, subject TEXT NOT NULL,
  email TEXT, created_at DOUBLE PRECISION NOT NULL, UNIQUE(issuer, subject));
CREATE INDEX IF NOT EXISTS ix_auth_identities_user ON auth_identities(user_id);
