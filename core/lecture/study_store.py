"""Persistence for knowledge items, study items, study sessions and progress (SQLite; same connection as store.py).

Grounding is enforced HERE, at write time, for every writer: each evidence link must (1) point at evidence of the
item's own lecture and (2) carry a `quote` that literally occurs in that evidence's text (whitespace/case-normalised).
Nothing can be persisted that cites another lecture's evidence or quotes text the evidence does not contain.

Regeneration is a keyed sync (dedupe_key), so progress/reviews attached to unchanged items survive; user edits and
user deletions (tombstones) are never overwritten or resurrected.
"""
import json
import re
import time
from typing import Dict, Iterable, List, Optional, Sequence

from .store import INTEGRITY_ERRORS, conn

_WS = re.compile(r"\s+")


def norm(s: Optional[str]) -> str:
    return _WS.sub(" ", (s or "").replace("“", '"').replace("”", '"').replace("’", "'")).strip().lower()


def _check_links(c, lecture_id: int, links: Sequence[dict]) -> List[dict]:
    out, seen = [], set()
    for ln in links:
        eid = ln["evidence_id"]
        if eid in seen:
            continue
        r = c.execute("SELECT lecture_id, text FROM lecture_evidence WHERE id=?", (eid,)).fetchone()
        if r is None or r["lecture_id"] != lecture_id:
            raise ValueError(f"citation {eid} is not evidence of lecture {lecture_id}")
        q = ln.get("quote")
        if q and norm(q) not in norm(r["text"]):
            raise ValueError(f"quote is not contained in evidence {eid}")
        seen.add(eid)
        out.append({"evidence_id": eid, "quote": q, "role": ln.get("role", "support")})
    return out


def _j(v):
    return None if v is None else json.dumps(v)


def _l(v):
    return json.loads(v) if v else None


# ── knowledge ────────────────────────────────────────────────────────────
def _fts_rebuild(c, lecture_id: int) -> None:
    c.execute("DELETE FROM knowledge_fts WHERE lecture_id=?", (lecture_id,))
    c.execute("INSERT INTO knowledge_fts(title,body,item_id,lecture_id) SELECT title, COALESCE(body,''), id, lecture_id "
              "FROM knowledge_items WHERE lecture_id=?", (lecture_id,))


def sync_knowledge(lecture_id: int, records: List[dict], fingerprint: str) -> dict:
    c = conn()
    with c:
        links = {r["dedupe_key"]: _check_links(c, lecture_id, r["evidence"]) for r in records}     # validates everything first
        tomb = {r[0] for r in c.execute("SELECT dedupe_key FROM knowledge_tombstones WHERE lecture_id=?", (lecture_id,))}
        existing = {r["dedupe_key"]: r["id"] for r in c.execute("SELECT id,dedupe_key FROM knowledge_items WHERE lecture_id=?", (lecture_id,))}
        want = {r["dedupe_key"] for r in records if r["dedupe_key"] not in tomb}
        gone = [i for k, i in existing.items() if k not in want]
        for i in gone:
            c.execute("DELETE FROM knowledge_evidence WHERE item_id=?", (i,))
            c.execute("DELETE FROM knowledge_reviews WHERE knowledge_id=?", (i,))
            c.execute("DELETE FROM knowledge_items WHERE id=?", (i,))
        ids, created = {}, 0
        for r in records:
            k = r["dedupe_key"]
            if k in tomb:
                continue
            vals = (r["kind"], r["title"], r.get("body"), r.get("concept_key"), r.get("slide_index"), r.get("start"), r.get("end"),
                    r["method"], r.get("confidence"))
            if k in existing:
                i = existing[k]
                c.execute("UPDATE knowledge_items SET kind=?,title=?,body=?,concept_key=?,slide_index=?,start=?,\"end\"=?,method=?,confidence=? WHERE id=?",
                          (*vals, i))
                c.execute("DELETE FROM knowledge_evidence WHERE item_id=?", (i,))
            else:
                i = c.execute("INSERT INTO knowledge_items(kind,title,body,concept_key,slide_index,start,\"end\",method,confidence,"
                              "lecture_id,dedupe_key,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                              (*vals, lecture_id, k, time.time())).lastrowid
                created += 1
            for n, ln in enumerate(links[k]):
                c.execute("INSERT INTO knowledge_evidence(item_id,evidence_id,position,role,quote) VALUES(?,?,?,?,?)",
                          (i, ln["evidence_id"], n, ln["role"], ln["quote"]))
            ids[k] = i
        _fts_rebuild(c, lecture_id)
        c.execute("INSERT OR REPLACE INTO knowledge_meta(lecture_id,fingerprint,generated_at) VALUES(?,?,?)", (lecture_id, fingerprint, time.time()))
    return {"ids": ids, "created": created, "removed": len(gone)}


def _k_rows(c, rows) -> List[dict]:
    out = []
    for r in rows:
        d = dict(r)
        d["evidence"] = [dict(x) for x in c.execute(
            "SELECT evidence_id, role, quote FROM knowledge_evidence WHERE item_id=? ORDER BY position", (d["id"],))]
        out.append(d)
    return out


def list_knowledge(lecture_ids: Sequence[int], kind: Optional[str] = None) -> List[dict]:
    if not lecture_ids:
        return []
    c = conn()
    ph = ",".join("?" * len(lecture_ids))
    q, args = f"SELECT * FROM knowledge_items WHERE lecture_id IN ({ph})", list(lecture_ids)
    if kind:
        q += " AND kind=?"
        args.append(kind)
    return _k_rows(c, c.execute(q + " ORDER BY lecture_id, COALESCE(slide_index, 9999), COALESCE(start, 0), id", args))


def get_knowledge(item_id: int) -> Optional[dict]:
    c = conn()
    rows = _k_rows(c, c.execute("SELECT * FROM knowledge_items WHERE id=?", (item_id,)).fetchall())
    return rows[0] if rows else None


def delete_knowledge(item_id: int) -> bool:
    """Deletes + tombstones (regeneration won't bring it back) + removes study items derived from it
    (unless the user edited them, which detaches them instead)."""
    c = conn()
    with c:
        r = c.execute("SELECT lecture_id, dedupe_key FROM knowledge_items WHERE id=?", (item_id,)).fetchone()
        if not r:
            return False
        c.execute("INSERT OR IGNORE INTO knowledge_tombstones(lecture_id,dedupe_key) VALUES(?,?)", (r["lecture_id"], r["dedupe_key"]))
        for sid in [x[0] for x in c.execute("SELECT id FROM study_items WHERE knowledge_id=? AND origin='generated'", (item_id,))]:
            _drop_study_item(c, sid)
        c.execute("UPDATE study_items SET knowledge_id=NULL WHERE knowledge_id=?", (item_id,))
        c.execute("DELETE FROM knowledge_evidence WHERE item_id=?", (item_id,))
        c.execute("DELETE FROM knowledge_reviews WHERE knowledge_id=?", (item_id,))
        c.execute("DELETE FROM knowledge_items WHERE id=?", (item_id,))
        _fts_rebuild(c, r["lecture_id"])
    return True


def knowledge_meta(lecture_id: int) -> Optional[dict]:
    r = conn().execute("SELECT fingerprint, generated_at FROM knowledge_meta WHERE lecture_id=?", (lecture_id,)).fetchone()
    return dict(r) if r else None


def search_knowledge(lecture_ids: Sequence[int], query: str, limit: int = 20) -> List[dict]:
    terms = ["".join(ch for ch in t if ch.isalnum()) for t in query.split()]
    terms = [t for t in terms if t]
    if not terms or not lecture_ids:
        return []
    c = conn()
    ph = ",".join("?" * len(lecture_ids))
    fts = " OR ".join(f'"{t}"' for t in terms)
    from . import store as _store
    if _store.DIALECT == "postgres":
        from . import pg
        ids = pg.search_knowledge_ids(c, list(lecture_ids), terms, limit)
    else:
        ids = [r["item_id"] for r in c.execute(
            f"SELECT item_id FROM knowledge_fts WHERE knowledge_fts MATCH ? AND lecture_id IN ({ph}) ORDER BY bm25(knowledge_fts) LIMIT ?",
            (fts, *lecture_ids, limit))]
    if not ids:
        return []
    rows = _k_rows(c, c.execute(f"SELECT * FROM knowledge_items WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall())
    order = {i: n for n, i in enumerate(ids)}
    return sorted(rows, key=lambda r: order[r["id"]])


def mark_reviewed(user_id: int, knowledge_id: int) -> dict:
    c = conn()
    with c:
        c.execute("INSERT INTO knowledge_reviews(user_id,knowledge_id,count,reviewed_at) VALUES(?,?,1,?) "
                  "ON CONFLICT(user_id,knowledge_id) DO UPDATE SET count=knowledge_reviews.count+1, reviewed_at=excluded.reviewed_at",
                  (user_id, knowledge_id, time.time()))
    r = c.execute("SELECT count, reviewed_at FROM knowledge_reviews WHERE user_id=? AND knowledge_id=?", (user_id, knowledge_id)).fetchone()
    return dict(r)


def reviewed_map(user_id: int, knowledge_ids: Sequence[int]) -> Dict[int, dict]:
    if not knowledge_ids:
        return {}
    ph = ",".join("?" * len(knowledge_ids))
    return {r["knowledge_id"]: {"count": r["count"], "reviewed_at": r["reviewed_at"]} for r in conn().execute(
        f"SELECT knowledge_id,count,reviewed_at FROM knowledge_reviews WHERE user_id=? AND knowledge_id IN ({ph})", (user_id, *knowledge_ids))}


# ── AI study artifact ────────────────────────────────────────────────────
def get_ai_study(lecture_id: int) -> Optional[dict]:
    r = conn().execute("SELECT fingerprint, provider, model, content, created_at FROM lecture_ai_study WHERE lecture_id=?",
                       (lecture_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["content"] = _l(d["content"])
    return d


def save_ai_study(lecture_id: int, fingerprint: str, provider: str, model: str, content: dict) -> None:
    c = conn()
    with c:
        c.execute("INSERT OR REPLACE INTO lecture_ai_study(lecture_id,fingerprint,provider,model,content,created_at) VALUES(?,?,?,?,?,?)",
                  (lecture_id, fingerprint, provider, model, json.dumps(content), time.time()))


# ── study items ──────────────────────────────────────────────────────────
def _s_row(c, r) -> dict:
    d = dict(r)
    d["options"], d["key_points"] = _l(d["options"]), _l(d["key_points"])
    d["evidence"] = [dict(x) for x in c.execute(
        "SELECT evidence_id, role, quote FROM study_item_evidence WHERE item_id=? ORDER BY position", (d["id"],))]
    return d


def _drop_study_item(c, item_id: int) -> None:
    for t in ("study_item_evidence",):
        c.execute(f"DELETE FROM {t} WHERE item_id=?", (item_id,))
    for t in ("study_session_items", "study_attempts", "study_progress"):
        c.execute(f"DELETE FROM {t} WHERE item_id=?", (item_id,))
    c.execute("DELETE FROM study_items WHERE id=?", (item_id,))


def _write_item(c, lecture_id: int, r: dict, item_id: Optional[int], origin: str = "generated") -> int:
    links = _check_links(c, lecture_id, r["evidence"])
    if not links:
        raise ValueError("a study item needs at least one supporting evidence link")
    now = time.time()
    vals = (r["kind"], r["subtype"], r["difficulty"], r["prompt"], r["answer"], r.get("explanation"), _j(r.get("options")),
            r.get("correct_option"), _j(r.get("key_points")), r.get("knowledge_id"), r.get("concept_key"), r.get("slide_index"))
    if item_id is None:
        item_id = c.execute("INSERT INTO study_items(kind,subtype,difficulty,prompt,answer,explanation,options,correct_option,key_points,"
                            "knowledge_id,concept_key,slide_index,lecture_id,dedupe_key,origin,created_at,updated_at) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (*vals, lecture_id, r["dedupe_key"], origin, now, now)).lastrowid
    else:
        c.execute("UPDATE study_items SET kind=?,subtype=?,difficulty=?,prompt=?,answer=?,explanation=?,options=?,correct_option=?,"
                  "key_points=?,knowledge_id=?,concept_key=?,slide_index=?,updated_at=? WHERE id=?", (*vals, now, item_id))
        c.execute("DELETE FROM study_item_evidence WHERE item_id=?", (item_id,))
    for n, ln in enumerate(links):
        c.execute("INSERT INTO study_item_evidence(item_id,evidence_id,position,role,quote) VALUES(?,?,?,?,?)",
                  (item_id, ln["evidence_id"], n, ln["role"], ln["quote"]))
    return item_id


def sync_study_items(lecture_id: int, records: List[dict], fingerprint: str, kinds: Optional[Sequence[str]] = None) -> dict:
    """Keyed sync of GENERATED study items. Unchanged keys keep their id (so progress survives); keys no longer
    produced are removed; user-edited items and user-deleted (tombstoned) keys are left alone."""
    c = conn()
    with c:
        existing = {r["dedupe_key"]: dict(r) for r in c.execute("SELECT id,dedupe_key,origin,status,kind FROM study_items WHERE lecture_id=?", (lecture_id,))}
        scope = lambda e: kinds is None or e["kind"] in kinds                                       # noqa: E731
        want = {r["dedupe_key"] for r in records}
        removed = 0
        for k, e in existing.items():
            if e["origin"] == "generated" and e["status"] == "active" and scope(e) and k not in want:
                _drop_study_item(c, e["id"])
                removed += 1
        created = updated = 0
        ids = {}
        for r in records:
            e = existing.get(r["dedupe_key"])
            if e is None:
                ids[r["dedupe_key"]] = _write_item(c, lecture_id, r, None)
                created += 1
            elif e["origin"] == "generated" and e["status"] == "active":
                ids[r["dedupe_key"]] = _write_item(c, lecture_id, r, e["id"])
                updated += 1
        c.execute("INSERT OR REPLACE INTO study_meta(lecture_id,fingerprint,generated_at) VALUES(?,?,?)", (lecture_id, fingerprint, time.time()))
    return {"ids": ids, "created": created, "updated": updated, "removed": removed}


def replace_item_content(item_id: int, record: dict) -> None:
    """Single-item regeneration: new content/links for an existing GENERATED item (id, progress and key preserved)."""
    c = conn()
    with c:
        r = c.execute("SELECT lecture_id FROM study_items WHERE id=?", (item_id,)).fetchone()
        if not r:
            raise ValueError("no such item")
        _write_item(c, r["lecture_id"], record, item_id)
        c.execute("UPDATE study_items SET regen_count=regen_count+1, origin='generated' WHERE id=?", (item_id,))


def list_study_items(lecture_ids: Sequence[int], kind: Optional[str] = None, difficulty: Optional[str] = None,
                     status: str = "active", ids: Optional[Sequence[int]] = None) -> List[dict]:
    if not lecture_ids:
        return []
    c = conn()
    q = f"SELECT * FROM study_items WHERE status=? AND lecture_id IN ({','.join('?' * len(lecture_ids))})"
    args: list = [status, *lecture_ids]
    for col, v in (("kind", kind), ("difficulty", difficulty)):
        if v:
            q += f" AND {col}=?"
            args.append(v)
    if ids is not None:
        q += f" AND id IN ({','.join('?' * len(ids)) or 'NULL'})"
        args += list(ids)
    return [_s_row(c, r) for r in c.execute(q + " ORDER BY lecture_id, COALESCE(slide_index, 9999), id", args)]


def get_study_item(item_id: int, include_deleted: bool = False) -> Optional[dict]:
    c = conn()
    r = c.execute("SELECT * FROM study_items WHERE id=?" + ("" if include_deleted else " AND status='active'"), (item_id,)).fetchone()
    return _s_row(c, r) if r else None


def update_study_item(item_id: int, fields: dict, links: Optional[List[dict]] = None) -> None:
    """User edit. Marks the item `edited` (it is then never overwritten by regeneration and is no longer claimed
    as machine-verified). Optional new evidence links are validated like any other write."""
    allowed = {"prompt", "answer", "explanation", "difficulty", "options", "correct_option", "key_points"}
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"cannot edit {sorted(bad)}")
    c = conn()
    with c:
        r = c.execute("SELECT lecture_id FROM study_items WHERE id=? AND status='active'", (item_id,)).fetchone()
        if not r:
            raise ValueError("no such item")
        sets, args = [], []
        for k, v in fields.items():
            sets.append(f"{k}=?")
            args.append(_j(v) if k in ("options", "key_points") else v)
        if sets:
            c.execute(f"UPDATE study_items SET {','.join(sets)}, origin='edited', updated_at=? WHERE id=?", (*args, time.time(), item_id))
        if links is not None:
            checked = _check_links(c, r["lecture_id"], links)
            if not checked:
                raise ValueError("a study item needs at least one supporting evidence link")
            c.execute("DELETE FROM study_item_evidence WHERE item_id=?", (item_id,))
            for n, ln in enumerate(checked):
                c.execute("INSERT INTO study_item_evidence(item_id,evidence_id,position,role,quote) VALUES(?,?,?,?,?)",
                          (item_id, ln["evidence_id"], n, ln["role"], ln["quote"]))
            c.execute("UPDATE study_items SET origin='edited', updated_at=? WHERE id=?", (time.time(), item_id))


def delete_study_item(item_id: int) -> bool:
    """Soft delete: the row stays as a tombstone so regeneration does not recreate it; progress is dropped."""
    c = conn()
    with c:
        if not c.execute("SELECT 1 FROM study_items WHERE id=? AND status='active'", (item_id,)).fetchone():
            return False
        c.execute("UPDATE study_items SET status='deleted', updated_at=? WHERE id=?", (time.time(), item_id))
        for t in ("study_session_items", "study_attempts", "study_progress"):
            c.execute(f"DELETE FROM {t} WHERE item_id=?", (item_id,))
    return True


def study_meta(lecture_id: int) -> Optional[dict]:
    r = conn().execute("SELECT fingerprint, generated_at FROM study_meta WHERE lecture_id=?", (lecture_id,)).fetchone()
    return dict(r) if r else None


# ── sessions + progress ──────────────────────────────────────────────────
def create_session(user_id: int, scope_type: str, scope_id: int, config: dict, item_ids: Sequence[int]) -> int:
    c = conn()
    with c:
        sid = c.execute("INSERT INTO study_sessions(user_id,scope_type,scope_id,config,created_at) VALUES(?,?,?,?,?)",
                        (user_id, scope_type, scope_id, json.dumps(config), time.time())).lastrowid
        c.executemany("INSERT INTO study_session_items(session_id,item_id,position) VALUES(?,?,?)", [(sid, i, n) for n, i in enumerate(item_ids)])
    return sid


def get_session(session_id: int) -> Optional[dict]:
    r = conn().execute("SELECT * FROM study_sessions WHERE id=?", (session_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["config"] = json.loads(d["config"] or "{}")
    return d


def session_item_ids(session_id: int) -> List[int]:
    """Only items that still exist and are active (deleted items silently leave the session)."""
    return [r[0] for r in conn().execute(
        "SELECT si.item_id FROM study_session_items si JOIN study_items i ON i.id=si.item_id "
        "WHERE si.session_id=? AND i.status='active' ORDER BY si.position", (session_id,))]


def add_attempt(session_id: int, item_id: int, user_id: int, correct: bool, self_grade: Optional[str] = None,
                selected_option: Optional[str] = None, response_text: Optional[str] = None) -> int:
    c = conn()
    try:
        with c:
            aid = c.execute("INSERT INTO study_attempts(session_id,item_id,user_id,correct,self_grade,selected_option,response_text,created_at) "
                            "VALUES(?,?,?,?,?,?,?,?)", (session_id, item_id, user_id, int(correct), self_grade, selected_option,
                                                        response_text, time.time())).lastrowid
            c.execute("INSERT INTO study_progress(user_id,item_id,attempts,correct_count,streak,last_correct,last_attempt_at) VALUES(?,?,1,?,?,?,?) "
                      "ON CONFLICT(user_id,item_id) DO UPDATE SET attempts=study_progress.attempts+1, correct_count=study_progress.correct_count+excluded.correct_count, "
                      "streak=CASE WHEN excluded.last_correct=1 THEN study_progress.streak+1 ELSE 0 END, last_correct=excluded.last_correct, "
                      "last_attempt_at=excluded.last_attempt_at",
                      (user_id, item_id, int(correct), int(correct), int(correct), time.time()))
    except INTEGRITY_ERRORS:
        raise ValueError("item already answered in this session")
    return aid


def session_attempts(session_id: int) -> Dict[int, dict]:
    return {r["item_id"]: dict(r) for r in conn().execute("SELECT * FROM study_attempts WHERE session_id=?", (session_id,))}


def finish_session(session_id: int) -> None:
    c = conn()
    with c:
        c.execute("UPDATE study_sessions SET finished_at=COALESCE(finished_at, ?) WHERE id=?", (time.time(), session_id))


def progress_rows(user_id: int, item_ids: Sequence[int]) -> Dict[int, dict]:
    if not item_ids:
        return {}
    ph = ",".join("?" * len(item_ids))
    return {r["item_id"]: dict(r) for r in conn().execute(f"SELECT * FROM study_progress WHERE user_id=? AND item_id IN ({ph})", (user_id, *item_ids))}


def session_counts(user_id: int, scope_type: str, scope_id: int) -> dict:
    r = conn().execute("SELECT COUNT(*) n, SUM(CASE WHEN finished_at IS NOT NULL THEN 1 ELSE 0 END) f FROM study_sessions WHERE user_id=? AND scope_type=? AND scope_id=?",
                       (user_id, scope_type, scope_id)).fetchone()
    return {"total": r["n"], "finished": r["f"] or 0}


def purge_lecture(c, lecture_id: int) -> None:
    """Called inside delete_lecture_data's transaction."""
    items = [r[0] for r in c.execute("SELECT id FROM study_items WHERE lecture_id=?", (lecture_id,))]
    for i in items:
        _drop_study_item(c, i)
    kids = [r[0] for r in c.execute("SELECT id FROM knowledge_items WHERE lecture_id=?", (lecture_id,))]
    for k in kids:
        c.execute("DELETE FROM knowledge_evidence WHERE item_id=?", (k,))
        c.execute("DELETE FROM knowledge_reviews WHERE knowledge_id=?", (k,))
    for t in ("knowledge_items", "knowledge_tombstones", "knowledge_meta", "study_meta", "lecture_ai_study"):
        c.execute(f"DELETE FROM {t} WHERE lecture_id=?", (lecture_id,))
    c.execute("DELETE FROM knowledge_fts WHERE lecture_id=?", (lecture_id,))
    c.execute("DELETE FROM study_session_items WHERE session_id IN (SELECT id FROM study_sessions WHERE scope_type='lecture' AND scope_id=?)", (lecture_id,))
    c.execute("DELETE FROM study_sessions WHERE scope_type='lecture' AND scope_id=?", (lecture_id,))
