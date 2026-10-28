"""Versioned, checksummed schema migrations for SQLite (dev/test) and PostgreSQL (production).

Files: core/lecture/migrations/<dialect>/NNNN_name.sql, applied in order, each in ONE transaction, recorded in
`schema_migrations(version, name, checksum, applied_at)`.

  * An applied migration whose file later changes is REJECTED (checksum drift) — fix forward with a new migration.
  * Concurrent starters are serialised (PostgreSQL advisory lock / SQLite BEGIN IMMEDIATE), so N API pods + workers
    may all call upgrade() at boot.
  * Directive `--@add_column <table> <column> <decl>` adds a column only if missing (SQLite has no
    ADD COLUMN IF NOT EXISTS; this keeps databases made by Sprint 1-4 code upgradeable).
  * Statements are split on `;` at end-of-line after `--` comments are stripped: no `;` inside string literals.

CLI:  python -m core.lecture.migrations status|upgrade   (uses NOTIV_DATABASE_URL, else NOTIV_LECTURE_DB)
"""
import hashlib
import os
import re
import sys
import time
from typing import List, NamedTuple

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")
_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")
ADVISORY_LOCK_ID = 7_272_727_001


class MigrationError(Exception):
    pass


class Migration(NamedTuple):
    version: int
    name: str
    checksum: str
    sql: str


def load(dialect: str) -> List[Migration]:
    d = os.path.join(HERE, dialect)
    out = []
    for f in sorted(os.listdir(d)):
        m = _NAME.match(f)
        if not m:
            continue
        with open(os.path.join(d, f), "rb") as fh:
            raw = fh.read()
        out.append(Migration(int(m.group(1)), m.group(2), hashlib.sha256(raw).hexdigest(), raw.decode("utf-8")))
    versions = [m.version for m in out]
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError(f"{dialect}: migration versions must be contiguous from 1, got {versions}")
    return out


def head_version(dialect: str) -> int:
    ms = load(dialect)
    return ms[-1].version if ms else 0


def _steps(sql: str):
    """-> list of ("add_column", table, col, decl) | ("sql", statement)."""
    steps, buf = [], []
    for line in sql.splitlines():
        s = line.strip()
        if s.startswith("--@add_column"):
            parts = s.split(None, 4)                       # --@add_column table column decl...
            if len(parts) < 4:
                raise MigrationError(f"bad directive: {s}")
            steps.append(("add_column", parts[1], parts[2], parts[3] + (" " + parts[4] if len(parts) > 4 else "")))
            continue
        line = re.sub(r"--.*$", "", line)
        buf.append(line)
        if line.rstrip().endswith(";"):
            stmt = "\n".join(buf).strip().rstrip(";").strip()
            if stmt:
                steps.append(("sql", stmt))
            buf = []
    rest = "\n".join(buf).strip()
    if rest:
        steps.append(("sql", rest))
    return steps


def _has_column(conn, dialect: str, table: str, col: str) -> bool:
    if dialect == "sqlite":
        return col in {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    return conn.execute("SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() "
                        "AND table_name = ? AND column_name = ?", (table, col)).fetchone() is not None


def _ensure_table(conn, dialect: str) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                 "checksum TEXT NOT NULL, applied_at " + ("DOUBLE PRECISION" if dialect == "postgres" else "REAL") + " NOT NULL)")


def _read_applied(conn) -> dict:
    return {r[0]: (r[1], r[2]) for r in conn.execute("SELECT version, name, checksum FROM schema_migrations").fetchall()}


def applied(conn, dialect: str) -> dict:
    _ensure_table(conn, dialect)
    conn.commit()
    return _read_applied(conn)


def current_version(conn, dialect: str) -> int:
    a = applied(conn, dialect)
    return max(a) if a else 0


def status(conn, dialect: str) -> dict:
    ms, done = load(dialect), applied(conn, dialect)
    drift = [m.version for m in ms if m.version in done and done[m.version][1] != m.checksum]
    unknown = [v for v in done if v > len(ms)]
    return {"dialect": dialect, "current": max(done) if done else 0, "head": len(ms),
            "pending": [m.version for m in ms if m.version not in done], "drift": drift, "unknown_applied": unknown,
            "ok": not drift and not unknown and all(m.version in done for m in ms)}


def upgrade(conn, dialect: str) -> List[int]:
    """Applies pending migrations; returns the versions applied. Raises MigrationError on drift/unknown versions."""
    ms = load(dialect)
    lock = dialect == "postgres"
    if lock:
        conn.execute("SELECT pg_advisory_lock(?)", (ADVISORY_LOCK_ID,))
    ran: List[int] = []
    try:
        st = status(conn, dialect)
        if st["drift"]:
            raise MigrationError(f"applied migrations were modified after being applied: {st['drift']}")
        if st["unknown_applied"]:
            raise MigrationError(f"database is at a newer schema than this code: {st['unknown_applied']}")
        for m in ms:
            if m.version in _read_applied(conn):
                continue
            try:
                if dialect == "sqlite":
                    conn.execute("BEGIN IMMEDIATE")
                    if m.version in _read_applied(conn):          # lost the race to another process
                        conn.execute("ROLLBACK")
                        continue
                for step in _steps(m.sql):
                    if step[0] == "add_column":
                        _, t, c, decl = step
                        if not _has_column(conn, dialect, t, c):
                            conn.execute(f"ALTER TABLE {t} ADD COLUMN {c} {decl}")
                    else:
                        conn.execute(step[1])
                conn.execute("INSERT INTO schema_migrations(version,name,checksum,applied_at) VALUES(?,?,?,?)",
                             (m.version, m.name, m.checksum, time.time()))
                conn.commit()
                ran.append(m.version)
            except Exception as e:
                conn.rollback()
                raise MigrationError(f"migration {m.version:04d}_{m.name} failed and was rolled back: {type(e).__name__}: {e}")
    finally:
        if lock:
            conn.execute("SELECT pg_advisory_unlock(?)", (ADVISORY_LOCK_ID,))
            conn.commit()
    return ran


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    cmd = argv[0] if argv else "status"
    from . import store
    c = store.raw_connection()
    d = store.DIALECT
    if cmd == "upgrade":
        print(f"applied: {upgrade(c, d)}")
    print(status(c, d))
    return 0 if status(c, d)["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
