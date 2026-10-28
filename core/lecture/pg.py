"""PostgreSQL adapter for the store (selected by NOTIV_DATABASE_URL=postgresql://...).

STATUS: UNVERIFIED against a running application. psycopg is not installable in the authoring sandbox, so:
  * `translate()` (pure SQL rewriting) IS unit-tested (core/test_pg_translate.py);
  * the PostgreSQL DDL (migrations/postgres/*.sql) WAS applied to a real PostgreSQL 16 server with psql;
  * everything that needs a live connection (PgConnection, search, job claiming) has NOT been run here.
core/test_pg_contract.py runs the shared store contract against a real server when NOTIV_TEST_DATABASE_URL is set
(it SKIPS otherwise). Treat PostgreSQL support as unproven until that test passes in your environment.

Design: the store keeps its sqlite3-style call sites (`c.execute(sql, params)`, `?` placeholders, `row["col"]`,
`cur.lastrowid`, `with c:`). PgConnection translates them so store/study_store need no second implementation. FTS5 is
replaced by tsvector tables (see migration 0001): only the two search queries differ and live here.
"""
import os
import re
import threading
from typing import Optional, Sequence

# conflict targets for "INSERT OR REPLACE" (SQLite) -> "ON CONFLICT (...) DO UPDATE" (PostgreSQL)
_PK = {"workspace_members": ("workspace_id", "user_id"), "lecture_insight_meta": ("lecture_id",),
       "lecture_embed_meta": ("lecture_id",), "knowledge_meta": ("lecture_id",), "study_meta": ("lecture_id",),
       "lecture_ai_study": ("lecture_id",)}
# tables whose INSERTs need the new BIGSERIAL id back (sqlite's cursor.lastrowid)
_SERIAL = {"workspaces", "courses", "lectures", "assets", "lecture_segments", "lecture_evidence", "jobs", "lecture_insights",
           "chat_conversations", "chat_messages", "knowledge_items", "study_items", "study_sessions", "study_attempts",
           "audit_log", "storage_deletions", "user_notes", "notebooks", "notebook_sources", "notebook_items", "notebook_shares"}
_INS = re.compile(r"^\s*INSERT\s+(OR\s+(REPLACE|IGNORE)\s+)?INTO\s+(\w+)\s*\(([^)]*)\)", re.I | re.S)


def _placeholders(sql: str) -> str:
    """'?' -> '%s' outside single-quoted literals; literal '%' -> '%%' (psycopg uses % for parameters)."""
    out, in_q = [], False
    for ch in sql:
        if ch == "'":
            in_q = not in_q
        if ch == "?" and not in_q:
            out.append("%s")
        elif ch == "%":
            out.append("%%")
        else:
            out.append(ch)
    return "".join(out)


def translate(sql: str) -> str:
    m = _INS.match(sql)
    tail = ""
    if m and m.group(2):
        kind, table, cols = m.group(2).upper(), m.group(3), [c.strip() for c in m.group(4).split(",")]
        sql = _INS.sub(lambda mm: f"INSERT INTO {table}({mm.group(4)})", sql, count=1)
        if kind == "IGNORE":
            tail = " ON CONFLICT DO NOTHING"
        else:
            pk = _PK[table]
            sets = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c not in pk)
            tail = f" ON CONFLICT ({', '.join(pk)}) DO UPDATE SET {sets}" if sets else " ON CONFLICT DO NOTHING"
    sql = _placeholders(sql.rstrip().rstrip(";")) + tail
    m2 = _INS.match(sql)
    if m2 and m2.group(3) in _SERIAL and "RETURNING" not in sql.upper() and "DO NOTHING" not in sql.upper():
        sql += " RETURNING id"
    return sql


class Row:
    """sqlite3.Row look-alike: row[0], row["col"], keys(), iteration over values, dict(row) works."""
    __slots__ = ("_k", "_v")

    def __init__(self, keys, values):
        self._k, self._v = keys, values

    def __getitem__(self, i):
        return self._v[i] if isinstance(i, int) else self._v[self._k.index(i)]

    def keys(self):
        return list(self._k)

    def __iter__(self):
        return iter(self._v)

    def __len__(self):
        return len(self._v)


class _Cursor:
    def __init__(self, cur, is_insert_returning: bool):
        self._c, self.lastrowid, self.rowcount = cur, None, cur.rowcount
        if is_insert_returning:
            r = cur.fetchone()
            self.lastrowid = r[0] if r else None

    def _rows(self, rs):
        keys = [d.name for d in self._c.description] if self._c.description else []
        return [Row(keys, tuple(r)) for r in rs]

    def fetchone(self):
        r = self._c.fetchone()
        return self._rows([r])[0] if r is not None else None

    def fetchall(self):
        return self._rows(self._c.fetchall())

    def __iter__(self):
        return iter(self.fetchall())


class PgConnection:
    """sqlite3.Connection look-alike over a psycopg (v3) connection."""

    def __init__(self, raw):
        self._raw = raw

    def is_broken(self) -> bool:
        """True if the underlying connection is closed/broken (server restart, idle timeout, network drop). The store
        drops such a cached connection and reconnects instead of failing every later call on this thread."""
        try:
            return bool(getattr(self._raw, "closed", False) or getattr(self._raw, "broken", False))
        except Exception:                        # noqa: BLE001
            return True

    def execute(self, sql, params: Sequence = ()):
        try:
            return self._execute(sql, params)
        except Exception:
            # PostgreSQL leaves the transaction ABORTED after any failed statement ("current transaction is aborted,
            # commands ignored until end of transaction block") — SQLite does not. Without this rollback a single caught
            # IntegrityError (e.g. the unique active-job index) would poison this thread's connection for every later call.
            try:
                self._raw.rollback()
            except Exception:                    # noqa: BLE001 — a dead connection can't roll back; is_broken() handles it
                pass
            raise

    def _execute(self, sql, params: Sequence = ()):
        s = sql.strip().upper()
        if s.startswith("BEGIN"):               # psycopg opens the transaction implicitly
            return _Cursor(self._raw.cursor(), False)
        if s in ("COMMIT", "ROLLBACK"):
            (self._raw.commit if s == "COMMIT" else self._raw.rollback)()
            return _Cursor(self._raw.cursor(), False)
        if s.startswith("PRAGMA"):
            return _Cursor(self._raw.cursor(), False)
        t = translate(sql)
        cur = self._raw.cursor()
        cur.execute(t, tuple(params))
        return _Cursor(cur, t.endswith("RETURNING id"))

    def executemany(self, sql, seq):
        try:
            cur = self._raw.cursor()
            cur.executemany(translate(sql), [tuple(p) for p in seq])
            return _Cursor(cur, False)
        except Exception:
            try:
                self._raw.rollback()
            except Exception:                    # noqa: BLE001
                pass
            raise

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        (self._raw.rollback if et else self._raw.commit)()
        return False


def connect(url: str) -> PgConnection:
    import psycopg  # lazy: only PostgreSQL deployments need it
    raw = psycopg.connect(url, connect_timeout=int(os.getenv("NOTIV_DB_CONNECT_TIMEOUT", "5")),
                          options="-c statement_timeout=" + os.getenv("NOTIV_DB_STATEMENT_TIMEOUT_MS", "30000"))
    schema = os.getenv("NOTIV_PG_SCHEMA")                # test isolation: one schema per test run
    if schema and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,40}", schema):
        raw.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        raw.execute(f'SET search_path TO "{schema}"')
        raw.commit()
    return PgConnection(raw)


# ── search (replaces FTS5 MATCH/bm25/snippet) ────────────────────────────
def _tsquery(terms: Sequence[str], op: str) -> str:
    return f" {op} ".join("".join(ch for ch in t if ch.isalnum()).lower() for t in terms if any(ch.isalnum() for ch in t))


def search_evidence(c, lecture_ids, terms, match: str, limit: int):
    q = _tsquery(terms, "|" if match == "any" else "&")
    if not q:
        return []
    ph = ",".join("?" * len(lecture_ids))
    rows = c.execute(
        "SELECT e.id AS evidence_id, e.lecture_id, e.source_type AS kind, e.source_id, e.slide_index, e.slide_id, "
        "e.source_asset_id, e.start, e.\"end\", e.confidence, e.text, "
        "ts_headline('english', e.text, to_tsquery('english', ?), 'StartSel=[,StopSel=],MaxWords=12,MinWords=3') AS snippet "
        f"FROM evidence_fts f JOIN lecture_evidence e ON e.id=f.evidence_id "
        f"WHERE f.tsv @@ to_tsquery('english', ?) AND e.lecture_id IN ({ph}) "
        "ORDER BY ts_rank_cd(f.tsv, to_tsquery('english', ?)) DESC, e.id LIMIT ?", (q, q, *lecture_ids, q, limit)).fetchall()
    return [dict(r) for r in rows]


def search_knowledge_ids(c, lecture_ids, terms, limit: int):
    q = _tsquery(terms, "|")
    if not q:
        return []
    ph = ",".join("?" * len(lecture_ids))
    return [r["item_id"] for r in c.execute(
        f"SELECT item_id FROM knowledge_fts WHERE tsv @@ to_tsquery('english', ?) AND lecture_id IN ({ph}) "
        "ORDER BY ts_rank_cd(tsv, to_tsquery('english', ?)) DESC LIMIT ?", (q, *lecture_ids, q, limit))]
