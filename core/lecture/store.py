"""SQLite implementation of the lecture persistence layer (dev/test default).

Services never import this directly: they use repo.LectureRepository, whose
SQLite implementation (repo.SqliteLectureRepository) delegates here. A
PostgreSQL(+pgvector) implementation targets the same interface.

Domain: workspaces -> courses -> lectures -> {assets, slides, segments,
timeline, evidence, jobs(+stages)}.  The DB holds storage KEYS, never
absolute filesystem paths.

Limitation: one SQLite file per process (use_db() switches it; thread-local
connections).
"""
import json
import os
import sqlite3
import threading
import time
from typing import List, Optional, Sequence

DB_PATH = os.getenv("NOTIV_LECTURE_DB", os.path.join("storage", "lectures.db"))
_local = threading.local()

SCHEMA = """
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
"""

# additive columns for databases created by earlier versions of this schema
MIGRATIONS = [
    ("courses", "workspace_id", "INTEGER"),
    ("lectures", "workspace_id", "INTEGER"),
    ("lectures", "course_id", "INTEGER"),
    ("lecture_slides", "id", "INTEGER"),
    ("lecture_slides", "image_key", "TEXT"),
    ("lecture_timeline", "confidence", "REAL"),
    ("lecture_segments", "confidence", "REAL"),
    ("lecture_segments", "manual_slide", "INTEGER DEFAULT 0"),   # 1 = student-corrected
    ("lecture_segments", "edited", "INTEGER DEFAULT 0"),
    ("lecture_evidence", "slide_id", "INTEGER"),
    ("lecture_evidence", "source_asset_id", "INTEGER"),
]


def use_db(path: str) -> None:
    global DB_PATH
    DB_PATH = path
    _local.__dict__.clear()


def _pg_url() -> str:
    from . import settings
    u = settings.database_url()
    return u if settings.is_postgres_url(u) else ""


DIALECT = "postgres" if _pg_url() else "sqlite"      # PostgreSQL path is UNVERIFIED here (see core/pg.py)
_pg_migrated = False


def raw_connection():
    if DIALECT == "postgres":
        from . import pg
        return pg.connect(_pg_url())
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def conn():
    global _pg_migrated
    path = DB_PATH if DIALECT == "sqlite" else _pg_url()
    c = getattr(_local, "c", None)
    if c is not None and getattr(c, "is_broken", lambda: False)():      # PostgreSQL: server restarted / connection dropped
        try:
            c.close()
        except Exception:                                               # noqa: BLE001
            pass
        c = _local.c = None
    if c is None or getattr(_local, "path", None) != path:
        from . import migrations
        if DIALECT == "sqlite":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        c = raw_connection()
        if DIALECT == "sqlite":
            c.execute("PRAGMA journal_mode=WAL")
        if DIALECT == "sqlite" or not _pg_migrated:
            migrations.upgrade(c, DIALECT)       # versioned, checksummed (replaces inline SCHEMA/MIGRATIONS)
            _pg_migrated = _pg_migrated or DIALECT == "postgres"
        c.commit()
        _local.c, _local.path = c, path
    return c


def _row(r):
    return dict(r) if r else None


# ── workspaces ───────────────────────────────────────────────────────────
def create_workspace(owner_id: int, name: str) -> int:
    c = conn()
    with c:
        cur = c.execute("INSERT INTO workspaces(name,owner_id,created_at) VALUES(?,?,?)", (name, owner_id, time.time()))
        c.execute("INSERT INTO workspace_members(workspace_id,user_id,role) VALUES(?,?,'owner')", (cur.lastrowid, owner_id))
    return cur.lastrowid


def ensure_personal_workspace(user_id: int) -> int:
    r = conn().execute("SELECT id FROM workspaces WHERE owner_id=? AND name='Personal' ORDER BY id LIMIT 1",
                       (user_id,)).fetchone()
    return r["id"] if r else create_workspace(user_id, "Personal")


def add_member(workspace_id: int, user_id: int, role: str = "member") -> None:
    c = conn()
    c.execute("INSERT OR REPLACE INTO workspace_members(workspace_id,user_id,role) VALUES(?,?,?)",
              (workspace_id, user_id, role))
    c.commit()


def is_member(workspace_id: int, user_id: int) -> bool:
    return conn().execute("SELECT 1 FROM workspace_members WHERE workspace_id=? AND user_id=?",
                          (workspace_id, user_id)).fetchone() is not None


# ── courses ──────────────────────────────────────────────────────────────
def create_course(owner_id: int, name: str, workspace_id: Optional[int] = None) -> int:
    ws = workspace_id or ensure_personal_workspace(owner_id)
    c = conn()
    cur = c.execute("INSERT INTO courses(owner_id,name,workspace_id,created_at) VALUES(?,?,?,?)",
                    (owner_id, name, ws, time.time()))
    c.commit()
    return cur.lastrowid


def get_course(course_id: int) -> Optional[dict]:
    return _row(conn().execute("SELECT * FROM courses WHERE id=?", (course_id,)).fetchone())


def list_courses_for_user(user_id: int, limit: int = 100, offset: int = 0) -> List[dict]:
    """List courses in workspaces the user belongs to; never return inaccessible workspace rows."""
    rows = conn().execute("""
        SELECT c.id, c.name, c.owner_id, c.workspace_id, c.created_at,
               COUNT(l.id) AS lecture_count,
               SUM(CASE WHEN l.status='done' THEN 1 ELSE 0 END) AS ready_count
        FROM courses c
        JOIN workspace_members wm ON wm.workspace_id=c.workspace_id AND wm.user_id=?
        LEFT JOIN lectures l ON l.course_id=c.id AND l.workspace_id=c.workspace_id
        GROUP BY c.id, c.name, c.owner_id, c.workspace_id, c.created_at
        ORDER BY c.created_at DESC, c.id DESC LIMIT ? OFFSET ?
    """, (user_id, limit, offset)).fetchall()
    return [dict(r) for r in rows]


def list_lectures_for_user(user_id: int, limit: int = 100, offset: int = 0,
                           course_id: Optional[int] = None) -> List[dict]:
    """List lecture metadata only within workspaces the caller can access."""
    sql = """SELECT l.id, l.title, l.status, l.stage, l.progress_pct, l.error, l.duration,
                    l.course_id, l.workspace_id, l.created_at,
                    c.name AS course_name,
                    (SELECT j.id FROM jobs j WHERE j.lecture_id=l.id ORDER BY j.id DESC LIMIT 1) AS job_id,
                    (SELECT j.status FROM jobs j WHERE j.lecture_id=l.id ORDER BY j.id DESC LIMIT 1) AS job_status
             FROM lectures l JOIN workspace_members wm ON wm.workspace_id=l.workspace_id AND wm.user_id=?
             LEFT JOIN courses c ON c.id=l.course_id AND c.workspace_id=l.workspace_id
             WHERE 1=1"""
    params = [user_id]
    if course_id is not None:
        sql += " AND l.course_id=?"
        params.append(course_id)
    sql += " ORDER BY l.created_at DESC, l.id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    return [dict(r) for r in conn().execute(sql, tuple(params)).fetchall()]


def course_lecture_ids(course_id: int, only_done: bool = True) -> List[int]:
    q = "SELECT id FROM lectures WHERE course_id=?" + (" AND status='done'" if only_done else "") + " ORDER BY id"
    return [r["id"] for r in conn().execute(q, (course_id,))]


# ── lectures ─────────────────────────────────────────────────────────────
def create_lecture(owner_id: int, title: str, language: Optional[str] = None, course_id: Optional[int] = None,
                   workspace_id: Optional[int] = None) -> int:
    ws = workspace_id or ensure_personal_workspace(owner_id)
    c = conn()
    cur = c.execute("INSERT INTO lectures(owner_id,title,language,course_id,workspace_id,created_at) VALUES(?,?,?,?,?,?)",
                    (owner_id, title, language, course_id, ws, time.time()))
    c.commit()
    return cur.lastrowid


def get_lecture(lecture_id: int) -> Optional[dict]:
    return _row(conn().execute("SELECT * FROM lectures WHERE id=?", (lecture_id,)).fetchone())


def set_status(lecture_id: int, status: str, stage: str = None, progress_pct: int = None,
               error: str = None, duration: float = None):
    sets, vals = ["status=?"], [status]
    for col, v in (("stage", stage), ("progress_pct", progress_pct), ("error", error), ("duration", duration)):
        if v is not None:
            sets.append(f"{col}=?")
            vals.append(v)
    if status in ("queued", "running", "done"):      # a fresh attempt/success clears a stale error
        if error is None:
            sets.append("error=NULL")
    c = conn()
    c.execute(f"UPDATE lectures SET {','.join(sets)} WHERE id=?", (*vals, lecture_id))
    c.commit()


# ── assets ───────────────────────────────────────────────────────────────
_ASSET_FIELDS = {"type", "storage_key", "original_filename", "mime_type", "size_bytes", "checksum",
                 "duration_seconds", "status", "uploader_id"}


def add_asset(lecture_id: int, workspace_id: int, **f) -> int:
    bad = set(f) - _ASSET_FIELDS
    if bad:
        raise ValueError(f"unknown asset fields: {sorted(bad)}")
    c = conn()
    cols = ["lecture_id", "workspace_id", "created_at", *f]
    cur = c.execute(f"INSERT INTO assets({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                    (lecture_id, workspace_id, time.time(), *f.values()))
    c.commit()
    return cur.lastrowid


def get_asset(asset_id: int) -> Optional[dict]:
    return _row(conn().execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone())


def list_assets(lecture_id: int, type: Optional[str] = None, status: Optional[str] = None) -> List[dict]:
    q, args = "SELECT * FROM assets WHERE lecture_id=?", [lecture_id]
    if type:
        q += " AND type=?"; args.append(type)
    if status:
        q += " AND status=?"; args.append(status)
    return [dict(r) for r in conn().execute(q + " ORDER BY id", args)]


def update_asset(asset_id: int, **f) -> None:
    bad = set(f) - _ASSET_FIELDS
    if bad or not f:
        raise ValueError(f"bad asset update fields: {sorted(bad)}")
    c = conn()
    c.execute(f"UPDATE assets SET {','.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), asset_id))
    c.commit()


# ── jobs & stages ────────────────────────────────────────────────────────
def _integrity_errors():
    if DIALECT == "postgres":
        import psycopg
        return (sqlite3.IntegrityError, psycopg.errors.IntegrityError)
    return (sqlite3.IntegrityError,)


INTEGRITY_ERRORS = _integrity_errors()
ACTIVE_JOB_STATES = ("queued", "running", "retrying")
WORKER_ID = os.getenv("NOTIV_WORKER_ID") or f"{os.uname().nodename}:{os.getpid()}"


def create_job(lecture_id: int, workspace_id: int, type: str, max_attempts: int = 3) -> int:
    c = conn()
    cur = c.execute("INSERT INTO jobs(lecture_id,workspace_id,type,max_attempts,created_at) VALUES(?,?,?,?,?)",
                    (lecture_id, workspace_id, type, max_attempts, time.time()))
    c.commit()
    return cur.lastrowid


def active_job(lecture_id: int, type: str) -> Optional[dict]:
    return _row(conn().execute("SELECT * FROM jobs WHERE lecture_id=? AND type=? AND status IN ('queued','running','retrying') "
                               "ORDER BY id LIMIT 1", (lecture_id, type)).fetchone())


def create_job_once(lecture_id: int, workspace_id: int, type: str, max_attempts: int = 3) -> tuple:
    """Idempotent enqueue -> (job_id, created). At most one active job per (lecture, type): enforced by the partial
    unique index ux_jobs_active, so two concurrent callers cannot both insert."""
    existing = active_job(lecture_id, type)
    if existing:
        return existing["id"], False
    try:
        return create_job(lecture_id, workspace_id, type, max_attempts), True
    except INTEGRITY_ERRORS:
        conn().rollback()
        existing = active_job(lecture_id, type)
        if existing:
            return existing["id"], False
        raise


def get_job(job_id: int) -> Optional[dict]:
    return _row(conn().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())


def list_jobs(lecture_id: int) -> List[dict]:
    return [dict(r) for r in conn().execute("SELECT * FROM jobs WHERE lecture_id=? ORDER BY id", (lecture_id,))]


_JOB_FIELDS = {"status", "attempt", "max_attempts", "progress", "error", "run_after", "heartbeat_at",
               "started_at", "finished_at", "worker_id", "error_class"}


def update_job(job_id: int, **f) -> None:
    bad = set(f) - _JOB_FIELDS
    if bad or not f:
        raise ValueError(f"bad job update fields: {sorted(bad)}")
    c = conn()
    c.execute(f"UPDATE jobs SET {','.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), job_id))
    c.commit()


def claim_next_job(now: float, job_id: Optional[int] = None, worker_id: Optional[str] = None) -> Optional[dict]:
    """Atomically move one runnable job (queued/retrying, due) to running, count the attempt and stamp the claiming
    worker (the lease owner). Safe against concurrent workers (BEGIN IMMEDIATE)."""
    c = conn()
    c.commit()
    c.execute("BEGIN IMMEDIATE")
    try:
        q = "SELECT id FROM jobs WHERE status IN ('queued','retrying') AND run_after<=?"
        args = [now]
        if job_id is not None:
            q += " AND id=?"; args.append(job_id)
        r = c.execute(q + " ORDER BY id LIMIT 1" + (" FOR UPDATE SKIP LOCKED" if DIALECT == "postgres" else ""), args).fetchone()
        if not r:
            c.execute("COMMIT")
            return None
        c.execute("UPDATE jobs SET status='running', attempt=attempt+1, started_at=COALESCE(started_at,?), "
                  "heartbeat_at=?, finished_at=NULL, worker_id=? WHERE id=?", (now, now, worker_id or WORKER_ID, r["id"]))
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise
    return get_job(r["id"])


def job_owned_by(job_id: int, worker_id: Optional[str] = None) -> bool:
    """Lease check (fencing): True only while the job is still 'running' under THIS worker. A worker whose job was
    recovered/re-claimed by another worker (or cancelled) must stop writing results."""
    r = conn().execute("SELECT status, worker_id FROM jobs WHERE id=?", (job_id,)).fetchone()
    return bool(r and r["status"] == "running" and r["worker_id"] == (worker_id or WORKER_ID))


def recover_stale_jobs(heartbeat_before: float) -> int:
    """Jobs left 'running' by a crashed worker: retry if attempts remain, else FAIL (poison-pill guard: a job that
    kills its worker every time must not loop forever)."""
    c = conn()
    now = time.time()
    with c:
        dead = c.execute("UPDATE jobs SET status='failed', finished_at=?, error_class='WorkerLost', "
                         "error='worker lost; attempts exhausted' WHERE status='running' AND COALESCE(heartbeat_at,0)<? "
                         "AND attempt>=max_attempts", (now, heartbeat_before)).rowcount
        ids = [r[0] for r in c.execute("SELECT lecture_id FROM jobs WHERE status='failed' AND error_class='WorkerLost' "
                                       "AND finished_at=?", (now,))]
        for lid in ids:
            c.execute("UPDATE lectures SET status='failed', stage='Failed', error='worker lost; attempts exhausted' WHERE id=?", (lid,))
        retry = c.execute("UPDATE jobs SET status='retrying', run_after=0, worker_id=NULL WHERE status='running' AND "
                          "COALESCE(heartbeat_at,0) < ?", (heartbeat_before,)).rowcount
    return dead + retry


def beat_worker(worker_id: Optional[str] = None, info: Optional[dict] = None) -> None:
    c = conn()
    now = time.time()
    with c:
        c.execute("INSERT INTO worker_heartbeats(worker_id,beat_at,started_at,info) VALUES(?,?,?,?) "
                  "ON CONFLICT(worker_id) DO UPDATE SET beat_at=excluded.beat_at, info=excluded.info",
                  (worker_id or WORKER_ID, now, now, json.dumps(info) if info else None))


def queue_stats(now: Optional[float] = None) -> dict:
    now = now or time.time()
    c = conn()
    by = {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}
    oldest = c.execute("SELECT MIN(created_at) m FROM jobs WHERE status IN ('queued','retrying') AND run_after<=?", (now,)).fetchone()["m"]
    workers = c.execute("SELECT COUNT(*) FROM worker_heartbeats WHERE beat_at>?", (now - 60,)).fetchone()[0]
    return {"by_status": by, "oldest_runnable_age_s": round(now - oldest, 1) if oldest else 0.0, "live_workers": workers}


def get_stage(job_id: int, stage: str) -> Optional[dict]:
    r = _row(conn().execute("SELECT * FROM job_stages WHERE job_id=? AND stage=?", (job_id, stage)).fetchone())
    if r and r["output"] is not None:
        r["output"] = json.loads(r["output"])
    return r


def list_stages(job_id: int) -> List[dict]:
    return [get_stage(job_id, r["stage"]) for r in
            conn().execute("SELECT stage FROM job_stages WHERE job_id=? ORDER BY " + ("seq" if DIALECT == "postgres" else "rowid"), (job_id,))]


def _set_stage_sql(c, job_id: int, stage: str, status: str, error: Optional[str] = None, output=None) -> None:
    now = time.time()
    cur = c.execute("SELECT attempt, started_at FROM job_stages WHERE job_id=? AND stage=?", (job_id, stage)).fetchone()
    if cur is None:
        c.execute("INSERT INTO job_stages(job_id,stage,status,attempt,started_at) VALUES(?,?,?,?,?)",
                  (job_id, stage, status, 1 if status == "running" else 0, now))
    elif status == "running":
        c.execute("UPDATE job_stages SET status=?, attempt=attempt+1, started_at=?, finished_at=NULL, error=NULL "
                  "WHERE job_id=? AND stage=?", (status, now, job_id, stage))
    if status != "running":
        c.execute("UPDATE job_stages SET status=?, error=?, output=?, finished_at=? WHERE job_id=? AND stage=?",
                  (status, error, None if output is None else json.dumps(output), now, job_id, stage))


def set_stage(job_id: int, stage: str, status: str, error: Optional[str] = None, output=None,
              owner: Optional[str] = None) -> bool:
    """owner=None: plain write. owner=<worker id>: FENCED write (see finalize_owned) — returns False, writing nothing,
    if that worker no longer holds the job's lease."""
    if owner is not None:
        return finalize_owned(job_id, owner, stage=(stage, status, error, output))
    c = conn()
    with c:
        _set_stage_sql(c, job_id, stage, status, error, output)
    return True


def finalize_owned(job_id: int, worker_id: Optional[str], job: Optional[dict] = None, lecture: Optional[dict] = None,
                   stage: Optional[tuple] = None) -> bool:
    """FENCED write: in ONE transaction, (1) lock the job row and verify it is still 'running' under `worker_id`,
    then (2) apply the stage result, the job update and the lecture status together. Returns False (and writes
    NOTHING) if the lease was lost. This closes the check-then-write race where a worker that passed an ownership
    check could still overwrite a job that was recovered/re-claimed/cancelled an instant later.
    job: job fields (store._JOB_FIELDS); lecture: {status, stage?, progress_pct?, error?}; stage: (name, status, error, output)."""
    if job and set(job) - _JOB_FIELDS:
        raise ValueError(f"bad job update fields: {sorted(set(job) - _JOB_FIELDS)}")
    c = conn()
    c.commit()
    c.execute("BEGIN IMMEDIATE")                     # sqlite: write lock up front; postgres: row lock via FOR UPDATE below
    try:
        r = c.execute("SELECT status, worker_id, lecture_id FROM jobs WHERE id=?" +
                      (" FOR UPDATE" if DIALECT == "postgres" else ""), (job_id,)).fetchone()
        if not (r and r["status"] == "running" and r["worker_id"] == (worker_id or WORKER_ID)):
            c.execute("ROLLBACK")
            return False
        if stage:
            _set_stage_sql(c, job_id, *stage)
        if job:
            c.execute(f"UPDATE jobs SET {','.join(k + '=?' for k in job)} WHERE id=?", (*job.values(), job_id))
        if lecture:
            sets, vals = ["status=?"], [lecture["status"]]
            for col in ("stage", "progress_pct", "error", "duration"):
                if lecture.get(col) is not None:
                    sets.append(f"{col}=?"); vals.append(lecture[col])
            if lecture["status"] in ("queued", "running", "done") and lecture.get("error") is None:
                sets.append("error=NULL")
            c.execute(f"UPDATE lectures SET {','.join(sets)} WHERE id=?", (*vals, r["lecture_id"]))
        c.execute("COMMIT")
        return True
    except Exception:
        c.execute("ROLLBACK")
        raise


# ── results (slides / timeline / segments) ───────────────────────────────
def save_results(lecture_id: int, slides: List[dict], timeline: List[dict], aligned_segments: List[dict]) -> dict:
    """Replaces slides/timeline/segments for a lecture in one transaction
    (idempotent: safe to re-run a stage). slides: [{index,key,text}],
    timeline: [{slide_index,start,end,confidence}]. Evidence is written
    separately (replace_evidence) and indexed by rebuild_index."""
    c = conn()
    with c:
        for t in ("lecture_slides", "lecture_segments", "lecture_timeline"):
            c.execute(f"DELETE FROM {t} WHERE lecture_id=?", (lecture_id,))
        slide_ids = {}
        for s in slides:
            sid = c.execute("SELECT COALESCE(MAX(id),0)+1 FROM lecture_slides").fetchone()[0]
            c.execute("INSERT INTO lecture_slides(lecture_id,idx,image_key,text,id) VALUES(?,?,?,?,?)",
                      (lecture_id, s["index"], s["key"], s["text"], sid))
            slide_ids[s["index"]] = sid
        for e in timeline:
            c.execute("INSERT INTO lecture_timeline(lecture_id,slide_index,start,\"end\",confidence) VALUES(?,?,?,?,?)",
                      (lecture_id, e["slide_index"], e["start"], e["end"], e.get("confidence")))
        seg_ids = []
        for g in aligned_segments:
            cur = c.execute("INSERT INTO lecture_segments(lecture_id,start,\"end\",text,slide_index,straddles,confidence)"
                            " VALUES(?,?,?,?,?,?,?)",
                            (lecture_id, g["start"], g["end"], g["text"], g["slide_index"], int(g["straddles"]),
                             g.get("confidence")))
            seg_ids.append(cur.lastrowid)
    return {"segment_ids": seg_ids, "slide_ids": slide_ids}


def replace_evidence(lecture_id: int, records: List[dict]) -> List[int]:
    c = conn()
    ids = []
    with c:
        c.execute("DELETE FROM evidence_fts WHERE lecture_id=?", (lecture_id,))
        c.execute("DELETE FROM lecture_evidence WHERE lecture_id=?", (lecture_id,))
        for r in records:
            ids.append(add_evidence(lecture_id, _c=c, **r))
    return ids


def add_evidence(lecture_id: int, source_type: str, source_id: int, text: str, start=None, end=None,
                 slide_index=None, slide_id=None, source_asset_id=None, confidence=None, _c=None) -> int:
    c = _c or conn()
    cur = c.execute("INSERT INTO lecture_evidence(lecture_id,source_type,source_id,start,\"end\",slide_index,slide_id,"
                    "source_asset_id,text,confidence,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (lecture_id, source_type, source_id, start, end, slide_index, slide_id, source_asset_id, text,
                     confidence, time.time()))
    if _c is None:
        c.commit()
    return cur.lastrowid


def rebuild_index(lecture_id: int) -> int:
    """(Re)builds the full-text index for a lecture from its evidence rows."""
    c = conn()
    with c:
        c.execute("DELETE FROM evidence_fts WHERE lecture_id=?", (lecture_id,))
        n = c.execute("INSERT INTO evidence_fts(text,evidence_id,lecture_id) "
                      "SELECT text,id,lecture_id FROM lecture_evidence WHERE lecture_id=?", (lecture_id,)).rowcount
    return n


def index_evidence(evidence_id: int) -> None:
    c = conn()
    with c:
        c.execute("DELETE FROM evidence_fts WHERE evidence_id=?", (evidence_id,))
        c.execute("INSERT INTO evidence_fts(text,evidence_id,lecture_id) SELECT text,id,lecture_id "
                  "FROM lecture_evidence WHERE id=?", (evidence_id,))


def viewer_data(lecture_id: int) -> dict:
    c = conn()
    lec = get_lecture(lecture_id)
    slides = [dict(r) for r in c.execute("SELECT id,idx,image_key,text FROM lecture_slides WHERE lecture_id=? ORDER BY idx", (lecture_id,))]
    segs = [dict(r) for r in c.execute(
        "SELECT s.id,s.start,s.end,s.text,s.slide_index,s.straddles,s.confidence,s.manual_slide,s.edited,"
        "(SELECT e.id FROM lecture_evidence e WHERE e.lecture_id=s.lecture_id AND e.source_type='transcript' "
        " AND e.source_id=s.id) AS evidence_id "
        "FROM lecture_segments s WHERE s.lecture_id=? ORDER BY s.start", (lecture_id,))]
    tl = [dict(r) for r in c.execute("SELECT slide_index,start,\"end\",confidence FROM lecture_timeline WHERE lecture_id=? ORDER BY start", (lecture_id,))]
    return {"lecture": {k: lec[k] for k in ("id", "title", "status", "duration", "course_id", "workspace_id")},
            "slides": slides, "timeline": tl, "segments": segs}


def get_evidence(evidence_id: int) -> Optional[dict]:
    return _row(conn().execute("SELECT * FROM lecture_evidence WHERE id=?", (evidence_id,)).fetchone())


def list_evidence(lecture_id: int) -> List[dict]:
    return [dict(r) for r in conn().execute("SELECT * FROM lecture_evidence WHERE lecture_id=? ORDER BY id", (lecture_id,))]


def _tokens(query: str) -> List[str]:
    return [t for t in "".join(ch if ch.isalnum() else " " for ch in query).split() if t]


_STOP = frozenset("a an the is are was were be been being of to in on at for and or but if then so as by with from "
                  "about into over under what why how when where which who whom whose does do did done can could "
                  "should would will may might must this that these those it its i you we they he she explain tell "
                  "me us please give show".split())


def search_evidence(lecture_ids: Sequence[int], query: str, limit: int = 20, match: str = "all") -> List[dict]:
    """BM25 search over evidence. `match="all"`: every term must appear;
    `match="any"`: stopwords dropped, any term may match (Q&A retrieval).
    Terms are always quoted, so user text can't inject FTS syntax."""
    terms = _tokens(query)
    if match == "any":
        terms = [t for t in terms if t.lower() not in _STOP] or terms
    if not terms or not lecture_ids:
        return []
    if DIALECT == "postgres":
        from . import pg
        return pg.search_evidence(conn(), list(lecture_ids), terms, match, limit)
    fts = (" OR " if match == "any" else " ").join(f'"{t}"' for t in terms)
    ph = ",".join("?" * len(lecture_ids))
    rows = conn().execute(
        f"SELECT e.id AS evidence_id, e.lecture_id, e.source_type AS kind, e.source_id, e.slide_index, e.slide_id,"
        f" e.source_asset_id, e.start, e.end, e.confidence, e.text, snippet(evidence_fts,0,'[',']','…',12) AS snippet "
        f"FROM evidence_fts JOIN lecture_evidence e ON e.id = evidence_fts.evidence_id "
        f"WHERE evidence_fts MATCH ? AND e.lecture_id IN ({ph}) ORDER BY bm25(evidence_fts) LIMIT ?",
        (fts, *lecture_ids, limit)).fetchall()
    return [dict(r) for r in rows]


def search(lecture_id: int, query: str, limit: int = 20) -> List[dict]:
    return search_evidence([lecture_id], query, limit, match="all")


# ── Notiv-generated insights (key moments, summary sections) ─────────────
def replace_insights(lecture_id: int, records: List[dict], fingerprint: str) -> List[int]:
    """Atomically replaces a lecture's generated insights. Each record:
    {kind,title,body,reason,method,start,\"end\",slide_index,evidence_ids:[...]}.
    Evidence ids must belong to the same lecture (enforced here)."""
    c = conn()
    ids = []
    with c:
        valid = {r["id"] for r in c.execute("SELECT id FROM lecture_evidence WHERE lecture_id=?", (lecture_id,))}
        c.execute("DELETE FROM insight_evidence WHERE insight_id IN (SELECT id FROM lecture_insights WHERE lecture_id=?)",
                  (lecture_id,))
        c.execute("DELETE FROM lecture_insights WHERE lecture_id=?", (lecture_id,))
        for pos, r in enumerate(records):
            bad = [e for e in r.get("evidence_ids", []) if e not in valid]
            if bad:
                raise ValueError(f"insight references evidence outside this lecture: {bad}")
            cur = c.execute("INSERT INTO lecture_insights(lecture_id,kind,title,body,reason,method,start,\"end\",slide_index,"
                            "position,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (lecture_id, r["kind"], r["title"], r.get("body"), r.get("reason"), r["method"],
                             r.get("start"), r.get("end"), r.get("slide_index"), pos, time.time()))
            ids.append(cur.lastrowid)
            for n, eid in enumerate(dict.fromkeys(r.get("evidence_ids", []))):
                c.execute("INSERT INTO insight_evidence(insight_id,evidence_id,position) VALUES(?,?,?)",
                          (cur.lastrowid, eid, n))
        c.execute("INSERT OR REPLACE INTO lecture_insight_meta(lecture_id,fingerprint,generated_at) VALUES(?,?,?)",
                  (lecture_id, fingerprint, time.time()))
    return ids


def insight_fingerprint(lecture_id: int) -> Optional[str]:
    r = conn().execute("SELECT fingerprint FROM lecture_insight_meta WHERE lecture_id=?", (lecture_id,)).fetchone()
    return r["fingerprint"] if r else None


def list_insights(lecture_id: int, kind: Optional[str] = None) -> List[dict]:
    c = conn()
    q, args = "SELECT * FROM lecture_insights WHERE lecture_id=?", [lecture_id]
    if kind:
        q += " AND kind=?"; args.append(kind)
    rows = [dict(r) for r in c.execute(q + " ORDER BY position", args)]
    for r in rows:
        r["evidence_ids"] = [x["evidence_id"] for x in c.execute(
            "SELECT evidence_id FROM insight_evidence WHERE insight_id=? ORDER BY position", (r["id"],))]
    return rows


def timeline_rows(lecture_id: int) -> List[dict]:
    return [dict(r) for r in conn().execute(
        "SELECT slide_index,start,\"end\",confidence FROM lecture_timeline WHERE lecture_id=? ORDER BY start", (lecture_id,))]


# ── embeddings (hybrid retrieval) ────────────────────────────────────────
def replace_embeddings(lecture_id: int, model: str, dim: int, rows: List[tuple], fingerprint: str) -> int:
    """rows: [(evidence_id, float32-bytes)]. Atomic; evidence must belong to the lecture."""
    c = conn()
    with c:
        valid = {r["id"] for r in c.execute("SELECT id FROM lecture_evidence WHERE lecture_id=?", (lecture_id,))}
        if any(eid not in valid for eid, _ in rows):
            raise ValueError("embedding references evidence outside this lecture")
        c.execute("DELETE FROM evidence_embeddings WHERE lecture_id=?", (lecture_id,))
        c.executemany("INSERT INTO evidence_embeddings(evidence_id,lecture_id,vec) VALUES(?,?,?)",
                      [(eid, lecture_id, blob) for eid, blob in rows])
        c.execute("INSERT OR REPLACE INTO lecture_embed_meta(lecture_id,model,dim,fingerprint,created_at) VALUES(?,?,?,?,?)",
                  (lecture_id, model, dim, fingerprint, time.time()))
    return len(rows)


def embedding_meta(lecture_id: int) -> Optional[dict]:
    r = conn().execute("SELECT model,dim,fingerprint FROM lecture_embed_meta WHERE lecture_id=?", (lecture_id,)).fetchone()
    return dict(r) if r else None


def load_embeddings(lecture_id: int) -> List[tuple]:
    """Only rows whose evidence still exists (joined), so stale vectors can never surface."""
    return [(r["evidence_id"], r["vec"]) for r in conn().execute(
        "SELECT v.evidence_id, v.vec FROM evidence_embeddings v JOIN lecture_evidence e ON e.id=v.evidence_id "
        "WHERE v.lecture_id=? AND e.lecture_id=?", (lecture_id, lecture_id))]


# ── Ask Notiv chat ───────────────────────────────────────────────────────
CHAT_MAX_MESSAGES = 200


def chat_conversation(lecture_id: int, user_id: int, create: bool = False) -> Optional[dict]:
    c = conn()
    r = c.execute("SELECT * FROM chat_conversations WHERE lecture_id=? AND user_id=?", (lecture_id, user_id)).fetchone()
    if r is None and create:
        now = time.time()
        with c:
            c.execute("INSERT OR IGNORE INTO chat_conversations(lecture_id,user_id,created_at,updated_at) VALUES(?,?,?,?)",
                      (lecture_id, user_id, now, now))
        r = c.execute("SELECT * FROM chat_conversations WHERE lecture_id=? AND user_id=?", (lecture_id, user_id)).fetchone()
    return dict(r) if r else None


def chat_add_message(conversation_id: int, role: str, content: str, grounded: Optional[bool] = None,
                     meta: Optional[dict] = None, evidence_ids: Optional[List[int]] = None) -> int:
    import json
    if role not in ("user", "assistant"):
        raise ValueError("bad role")
    c = conn()
    with c:
        conv = c.execute("SELECT lecture_id FROM chat_conversations WHERE id=?", (conversation_id,)).fetchone()
        if not conv:
            raise ValueError("no such conversation")
        ev = {}
        for eid in dict.fromkeys(evidence_ids or []):
            r = c.execute("SELECT id,source_type,start,slide_index,text,lecture_id FROM lecture_evidence WHERE id=?", (eid,)).fetchone()
            if r is None or r["lecture_id"] != conv["lecture_id"]:     # a citation must be real evidence of THIS lecture
                raise ValueError(f"citation {eid} is not evidence of this lecture")
            ev[eid] = {"source_type": r["source_type"], "start": r["start"], "slide_index": r["slide_index"], "text": r["text"][:600]}
        now = time.time()
        cur = c.execute("INSERT INTO chat_messages(conversation_id,role,content,grounded,meta,created_at) VALUES(?,?,?,?,?,?)",
                        (conversation_id, role, content, None if grounded is None else int(grounded),
                         json.dumps(meta) if meta else None, now))
        for n, (eid, snap) in enumerate(ev.items()):
            c.execute("INSERT INTO chat_message_evidence(message_id,evidence_id,position,snapshot) VALUES(?,?,?,?)",
                      (cur.lastrowid, eid, n, json.dumps(snap)))
        c.execute("UPDATE chat_conversations SET updated_at=? WHERE id=?", (now, conversation_id))
        extra = c.execute("SELECT COUNT(*) FROM chat_messages WHERE conversation_id=?", (conversation_id,)).fetchone()[0] - CHAT_MAX_MESSAGES
        if extra > 0:                                                  # bounded history: drop the oldest
            old = [r[0] for r in c.execute("SELECT id FROM chat_messages WHERE conversation_id=? ORDER BY id LIMIT ?", (conversation_id, extra))]
            c.executemany("DELETE FROM chat_message_evidence WHERE message_id=?", [(i,) for i in old])
            c.executemany("DELETE FROM chat_messages WHERE id=?", [(i,) for i in old])
    return cur.lastrowid


def chat_messages(conversation_id: int, limit: Optional[int] = None) -> List[dict]:
    import json
    c = conn()
    q = "SELECT * FROM chat_messages WHERE conversation_id=? ORDER BY id"
    rows = [dict(r) for r in c.execute(q, (conversation_id,))]
    if limit:
        rows = rows[-limit:]
    for r in rows:
        r["grounded"] = None if r["grounded"] is None else bool(r["grounded"])
        r["meta"] = json.loads(r["meta"]) if r["meta"] else {}
        r["evidence"] = [{"evidence_id": x["evidence_id"], "snapshot": json.loads(x["snapshot"]) if x["snapshot"] else None}
                         for x in c.execute("SELECT evidence_id,snapshot FROM chat_message_evidence WHERE message_id=? ORDER BY position", (r["id"],))]
    return rows


def chat_clear(conversation_id: int) -> int:
    c = conn()
    with c:
        ids = [r[0] for r in c.execute("SELECT id FROM chat_messages WHERE conversation_id=?", (conversation_id,))]
        c.executemany("DELETE FROM chat_message_evidence WHERE message_id=?", [(i,) for i in ids])
        c.execute("DELETE FROM chat_messages WHERE conversation_id=?", (conversation_id,))
    return len(ids)


def chat_count_since(conversation_id: int, role: str, since: float) -> int:
    return conn().execute("SELECT COUNT(*) FROM chat_messages WHERE conversation_id=? AND role=? AND created_at>=?",
                          (conversation_id, role, since)).fetchone()[0]


# ── manual corrections ───────────────────────────────────────────────────
def update_segment_text(lecture_id: int, segment_id: int, text: str) -> bool:
    text = text.strip()
    if not text:
        raise ValueError("segment text cannot be empty")
    c = conn()
    with c:
        cur = c.execute("UPDATE lecture_segments SET text=?, edited=1 WHERE id=? AND lecture_id=?",
                        (text, segment_id, lecture_id))
        if cur.rowcount == 0:
            return False
        ev = c.execute("SELECT id FROM lecture_evidence WHERE lecture_id=? AND source_type='transcript' AND source_id=?",
                       (lecture_id, segment_id)).fetchone()
        c.execute("UPDATE lecture_evidence SET text=? WHERE id=?", (text, ev["id"]))
        c.execute("DELETE FROM evidence_fts WHERE evidence_id=?", (ev["id"],))
        c.execute("INSERT INTO evidence_fts(text,evidence_id,lecture_id) VALUES(?,?,?)", (text, ev["id"], lecture_id))
    return True


def set_segment_slide(lecture_id: int, segment_id: int, slide_index: Optional[int], manual: bool = True,
                      confidence: Optional[float] = None) -> bool:
    c = conn()
    slide_id = None
    if slide_index is not None:
        r = c.execute("SELECT id FROM lecture_slides WHERE lecture_id=? AND idx=?", (lecture_id, slide_index)).fetchone()
        if not r:
            raise ValueError(f"slide {slide_index} does not exist in this lecture")
        slide_id = r["id"]
    conf = 1.0 if (manual and slide_index) else confidence
    with c:
        cur = c.execute("UPDATE lecture_segments SET slide_index=?, manual_slide=?, confidence=? "
                        "WHERE id=? AND lecture_id=?", (slide_index, int(manual), conf, segment_id, lecture_id))
        if cur.rowcount == 0:
            return False
        c.execute("UPDATE lecture_evidence SET slide_index=?, slide_id=?, confidence=? WHERE lecture_id=? "
                  "AND source_type='transcript' AND source_id=?", (slide_index, slide_id, conf, lecture_id, segment_id))
    return True


def replace_timeline(lecture_id: int, entries: List[dict]) -> None:
    c = conn()
    with c:
        c.execute("DELETE FROM lecture_timeline WHERE lecture_id=?", (lecture_id,))
        for e in entries:
            c.execute("INSERT INTO lecture_timeline(lecture_id,slide_index,start,\"end\",confidence) VALUES(?,?,?,?,?)",
                      (lecture_id, e["slide_index"], e["start"], e["end"], e.get("confidence")))


def segments_for_realign(lecture_id: int) -> List[dict]:
    return [dict(r) for r in conn().execute(
        "SELECT id,start,\"end\",text,manual_slide FROM lecture_segments WHERE lecture_id=? ORDER BY start", (lecture_id,))]


def slide_count(lecture_id: int) -> int:
    return conn().execute("SELECT COUNT(*) FROM lecture_slides WHERE lecture_id=?", (lecture_id,)).fetchone()[0]


def get_slide(lecture_id: int, idx: int) -> Optional[dict]:
    return _row(conn().execute("SELECT * FROM lecture_slides WHERE lecture_id=? AND idx=?", (lecture_id, idx)).fetchone())


def delete_lecture_data(lecture_id: int) -> None:
    """Removes every DB row belonging to a lecture and, in the SAME transaction, records the storage prefix in the
    deletion outbox (storage_deletions) so objects are removed even if the process dies right after commit."""
    c = conn()
    with c:
        c.execute("INSERT INTO storage_deletions(kind,target,created_at) VALUES('prefix',?,?)",
                  (f"lectures/{lecture_id}", time.time()))
        c.execute("DELETE FROM job_stages WHERE job_id IN (SELECT id FROM jobs WHERE lecture_id=?)", (lecture_id,))
        c.execute("DELETE FROM evidence_fts WHERE lecture_id=?", (lecture_id,))
        c.execute("DELETE FROM insight_evidence WHERE insight_id IN (SELECT id FROM lecture_insights WHERE lecture_id=?)",
                  (lecture_id,))
        c.execute("DELETE FROM lecture_insight_meta WHERE lecture_id=?", (lecture_id,))
        c.execute("DELETE FROM chat_message_evidence WHERE message_id IN (SELECT m.id FROM chat_messages m "
                  "JOIN chat_conversations v ON v.id=m.conversation_id WHERE v.lecture_id=?)", (lecture_id,))
        c.execute("DELETE FROM chat_messages WHERE conversation_id IN (SELECT id FROM chat_conversations WHERE lecture_id=?)", (lecture_id,))
        c.execute("DELETE FROM manual_questions WHERE lecture_id=?", (lecture_id,))
        from . import study_store
        study_store.purge_lecture(c, lecture_id)
        from . import notes
        notes.purge_lecture(c, lecture_id)
        for t in ("chat_conversations", "evidence_embeddings", "lecture_embed_meta", "lecture_insights", "jobs", "assets", "lecture_slides", "lecture_segments", "lecture_timeline", "lecture_evidence"):
            c.execute(f"DELETE FROM {t} WHERE lecture_id=?", (lecture_id,))
        c.execute("DELETE FROM lectures WHERE id=?", (lecture_id,))
