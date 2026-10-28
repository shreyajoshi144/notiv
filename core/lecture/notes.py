"""Personal notes: free-text notes a user keeps about a lecture, optionally linked to evidence (transcript lines / slide text).

* PRIVATE to the author: even inside a shared workspace nobody else can list/read/change a note. A note is only reachable
  while its author can still access the lecture (membership is re-checked on every call).
* Anchored to ONE lecture; may carry a slide number and/or timestamp, and up to MAX_LINKS evidence links. Every linked
  evidence id is verified to belong to the SAME lecture (so a note can never point into another lecture's data).
* Links store a snapshot (quote + position) and have no foreign key: re-processing a lecture replaces its evidence rows,
  so a link whose evidence vanished is reported `available: false` (UI: "source replaced") instead of being lost.
* Errors follow the API contract: unknown / not-yours -> NotFoundError (indistinguishable); bad input -> ValidationError;
  per-lecture quota -> QuotaError. Deleting a lecture or erasing a user removes their notes (store/ops_store).
"""
import math
import time
from typing import List, Optional

from . import authz, settings
from .errors import NotFoundError, QuotaError, ValidationError
from .repo import LectureRepository
from . import store

MAX_BODY = 10_000
MAX_LINKS = 20
QUOTE_CHARS = 300
DEFAULT_QUOTA = 1000
_FIELDS = {"body", "evidence_ids", "slide_index", "t"}


def _quota() -> int:
    return settings.env_int("NOTIV_QUOTA_NOTES_PER_LECTURE", DEFAULT_QUOTA)


def _body(v) -> str:
    if not isinstance(v, str):
        raise ValidationError("note text must be a string")
    v = v.strip()
    if not v:
        raise ValidationError("note text is empty")
    if len(v) > MAX_BODY:
        raise ValidationError(f"note is too long (max {MAX_BODY} characters)")
    return v


def _slide(v) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 100_000:
        raise ValidationError("slide_index must be a positive integer")
    return v


def _time(v) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 10 ** 7:
        raise ValidationError("t must be a number of seconds >= 0")
    return float(v)


def _ids(v) -> List[int]:
    if v is None:
        return []
    if not isinstance(v, list):
        raise ValidationError("evidence_ids must be a list")
    out = []
    for i in v:
        if not authz.valid_id(i):
            raise ValidationError("unknown evidence id")
        if i not in out:
            out.append(i)
    if len(out) > MAX_LINKS:
        raise ValidationError(f"too many evidence links (max {MAX_LINKS})")
    return out


class NotesService:
    def __init__(self, repo: LectureRepository):
        self.repo = repo

    # ── internals ────────────────────────────────────────────────────
    def _c(self):
        getattr(self.repo, "_ensure", lambda: None)()          # repos pin the DB file (several can coexist in one process)
        return store.conn()

    def _own_note(self, user_id: int, note_id: int) -> dict:
        if not authz.valid_id(note_id):
            raise NotFoundError("not found")
        r = self._c().execute("SELECT * FROM user_notes WHERE id=?", (note_id,)).fetchone()
        if not r or r["user_id"] != user_id:
            raise NotFoundError("not found")                   # someone else's note == no note
        authz.get_lecture(self.repo, user_id, r["lecture_id"])  # author lost access to the lecture -> not reachable
        return dict(r)

    def _verify_evidence(self, lecture_id: int, ids: List[int]) -> List[dict]:
        rows = []
        for i in ids:
            ev = self.repo.get_evidence(i)
            if not ev or ev["lecture_id"] != lecture_id:
                raise ValidationError("unknown evidence id")    # same answer for 'missing' and 'other lecture'
            rows.append(ev)
        return rows

    def _links(self, note_ids: List[int]) -> dict:
        out = {n: [] for n in note_ids}
        if not note_ids:
            return out
        c = self._c()
        ph = ",".join("?" * len(note_ids))
        for r in c.execute(f"SELECT note_id, evidence_id, quote, slide_index, start FROM user_note_links WHERE note_id IN ({ph}) "
                           "ORDER BY note_id, evidence_id", tuple(note_ids)):
            out[r["note_id"]].append(dict(r))
        return out

    def _present(self, rows: List[dict]) -> List[dict]:
        links = self._links([r["id"] for r in rows])
        res = []
        for r in rows:
            ls = []
            for lk in links[r["id"]]:
                ev = self.repo.get_evidence(lk["evidence_id"])
                ok = bool(ev and ev["lecture_id"] == r["lecture_id"])
                ls.append({"evidence_id": lk["evidence_id"], "available": ok,
                           "source_changed": bool(ok and (ev["text"] or "")[:QUOTE_CHARS] != (lk["quote"] or "")),
                           "quote": (ev["text"][:QUOTE_CHARS] if ok else lk["quote"]), "slide_index": lk["slide_index"],
                           "start": lk["start"]})
            res.append({"id": r["id"], "lecture_id": r["lecture_id"], "body": r["body"], "slide_index": r["slide_index"],
                        "t": r["t"], "created_at": r["created_at"], "updated_at": r["updated_at"], "links": ls})
        return res

    def _write_links(self, c, note_id: int, evidence: List[dict]) -> None:
        c.execute("DELETE FROM user_note_links WHERE note_id=?", (note_id,))
        for ev in evidence:
            c.execute("INSERT INTO user_note_links(note_id,evidence_id,quote,slide_index,start) VALUES(?,?,?,?,?)",
                      (note_id, ev["id"], (ev["text"] or "")[:QUOTE_CHARS], ev.get("slide_index"), ev.get("start")))

    # ── API ──────────────────────────────────────────────────────────
    def create(self, user_id: int, lecture_id: int, body, evidence_ids=None, slide_index=None, t=None) -> dict:
        lec = authz.get_lecture(self.repo, user_id, lecture_id)
        body, slide_index, t = _body(body), _slide(slide_index), _time(t)
        evidence = self._verify_evidence(lec["id"], _ids(evidence_ids))
        c = self._c()
        lim = _quota()
        if lim > 0 and c.execute("SELECT COUNT(*) FROM user_notes WHERE user_id=? AND lecture_id=?",
                                 (user_id, lec["id"])).fetchone()[0] >= lim:
            raise QuotaError("note limit reached for this lecture", "notes_per_lecture", lim)
        now = time.time()
        with c:
            cur = c.execute("INSERT INTO user_notes(user_id,lecture_id,workspace_id,body,slide_index,t,created_at,updated_at) "
                            "VALUES(?,?,?,?,?,?,?,?)", (user_id, lec["id"], lec["workspace_id"], body, slide_index, t, now, now))
            nid = cur.lastrowid
            self._write_links(c, nid, evidence)
        return self.get(user_id, nid)

    def get(self, user_id: int, note_id: int) -> dict:
        return self._present([self._own_note(user_id, note_id)])[0]

    def list(self, user_id: int, lecture_id: int, after_id: int = 0, limit: int = 100, evidence_id: Optional[int] = None) -> dict:
        lec = authz.get_lecture(self.repo, user_id, lecture_id)
        if isinstance(after_id, bool) or not isinstance(after_id, int) or after_id < 0:
            raise ValidationError("after_id must be an integer >= 0")
        limit = min(max(int(limit), 1), 200)
        q = "SELECT * FROM user_notes WHERE user_id=? AND lecture_id=? AND id>?"
        args: list = [user_id, lec["id"], after_id]
        if evidence_id is not None:
            if not authz.valid_id(evidence_id):
                raise ValidationError("unknown evidence id")
            q += " AND id IN (SELECT note_id FROM user_note_links WHERE evidence_id=?)"
            args.append(evidence_id)
        rows = [dict(r) for r in self._c().execute(q + " ORDER BY id LIMIT ?", (*args, limit + 1)).fetchall()]
        more = len(rows) > limit
        rows = rows[:limit]
        return {"lecture_id": lec["id"], "notes": self._present(rows), "has_more": more,
                "next_after_id": rows[-1]["id"] if more else None}

    def update(self, user_id: int, note_id: int, **fields) -> dict:
        bad = set(fields) - _FIELDS
        if bad or not fields:
            raise ValidationError("nothing to update" if not fields else f"unknown fields: {sorted(bad)}")
        note = self._own_note(user_id, note_id)
        sets, vals = [], []
        if "body" in fields:
            sets.append("body=?"); vals.append(_body(fields["body"]))
        if "slide_index" in fields:
            sets.append("slide_index=?"); vals.append(_slide(fields["slide_index"]))
        if "t" in fields:
            sets.append("t=?"); vals.append(_time(fields["t"]))
        evidence = self._verify_evidence(note["lecture_id"], _ids(fields["evidence_ids"])) if "evidence_ids" in fields else None
        c = self._c()
        with c:
            c.execute(f"UPDATE user_notes SET {','.join(sets + ['updated_at=?'])} WHERE id=? AND user_id=?",
                      (*vals, time.time(), note_id, user_id))
            if evidence is not None:
                self._write_links(c, note_id, evidence)
        return self.get(user_id, note_id)

    def delete(self, user_id: int, note_id: int) -> None:
        self._own_note(user_id, note_id)
        c = self._c()
        with c:
            c.execute("DELETE FROM user_note_links WHERE note_id=?", (note_id,))
            c.execute("DELETE FROM user_notes WHERE id=? AND user_id=?", (note_id, user_id))


def purge_lecture(c, lecture_id: int) -> None:
    """Called inside store.delete_lecture_data's transaction."""
    c.execute("DELETE FROM user_note_links WHERE note_id IN (SELECT id FROM user_notes WHERE lecture_id=?)", (lecture_id,))
    c.execute("DELETE FROM user_notes WHERE lecture_id=?", (lecture_id,))


def purge_user(c, user_id: int) -> int:
    """Right to erasure: every note the user wrote, in any lecture. Called inside ops_store.purge_user_rows."""
    c.execute("DELETE FROM user_note_links WHERE note_id IN (SELECT id FROM user_notes WHERE user_id=?)", (user_id,))
    return c.execute("DELETE FROM user_notes WHERE user_id=?", (user_id,)).rowcount
