"""Operational tables (migration 0002): idempotency keys, rate-limit counters, audit log, storage-deletion outbox,
usage counts for quotas, and the right-to-erasure purge of a user's rows. SQL only; policy lives in the callers."""
import json
import time
from typing import List, Optional

from .store import INTEGRITY_ERRORS, conn


# ── idempotency ──────────────────────────────────────────────────────────
def idem_begin(user_id: int, scope: str, key: str, request_hash: str) -> dict:
    """-> {"state": "new"} | {"state": "replay", status, body} | {"state": "conflict"} | {"state": "in_progress"}."""
    c = conn()
    try:
        with c:
            c.execute("INSERT INTO idempotency_keys(user_id,scope,key,request_hash,created_at) VALUES(?,?,?,?,?)",
                      (user_id, scope, key, request_hash, time.time()))
        return {"state": "new"}
    except INTEGRITY_ERRORS:
        r = c.execute("SELECT * FROM idempotency_keys WHERE user_id=? AND scope=? AND key=?", (user_id, scope, key)).fetchone()
        if r is None:
            return {"state": "in_progress"}
        if r["request_hash"] != request_hash:
            return {"state": "conflict"}
        if r["state"] == "done":
            return {"state": "replay", "status": r["response_status"], "body": json.loads(r["response_body"] or "null")}
        return {"state": "in_progress"}


def idem_finish(user_id: int, scope: str, key: str, status: int, body) -> None:
    c = conn()
    with c:
        c.execute("UPDATE idempotency_keys SET state='done', response_status=?, response_body=? WHERE user_id=? AND scope=? AND key=?",
                  (status, json.dumps(body), user_id, scope, key))


def idem_abandon(user_id: int, scope: str, key: str) -> None:
    """The request failed: release the key so the client can retry with the same Idempotency-Key."""
    c = conn()
    with c:
        c.execute("DELETE FROM idempotency_keys WHERE user_id=? AND scope=? AND key=? AND state='in_progress'", (user_id, scope, key))


def idem_purge(older_than: float) -> int:
    c = conn()
    with c:
        return c.execute("DELETE FROM idempotency_keys WHERE created_at<?", (older_than,)).rowcount


# ── rate limiting (db backend) ───────────────────────────────────────────
def rl_hit(bucket: str, window_start: int) -> int:
    c = conn()
    with c:
        c.execute("INSERT INTO rate_limits(bucket,window_start,count) VALUES(?,?,1) "
                  "ON CONFLICT(bucket,window_start) DO UPDATE SET count=rate_limits.count+1", (bucket, window_start))
        return c.execute("SELECT count FROM rate_limits WHERE bucket=? AND window_start=?", (bucket, window_start)).fetchone()[0]


def rl_purge(before_window: int) -> int:
    c = conn()
    with c:
        return c.execute("DELETE FROM rate_limits WHERE window_start<?", (before_window,)).rowcount


# ── audit ────────────────────────────────────────────────────────────────
def audit(user_id: Optional[int], action: str, resource_type: Optional[str] = None, resource_id: Optional[int] = None,
          workspace_id: Optional[int] = None, request_id: Optional[str] = None, detail: Optional[dict] = None) -> None:
    """Best-effort: an audit failure must never fail the user's request."""
    try:
        c = conn()
        with c:
            c.execute("INSERT INTO audit_log(ts,user_id,action,resource_type,resource_id,workspace_id,request_id,detail) VALUES(?,?,?,?,?,?,?,?)",
                      (time.time(), user_id, action, resource_type, resource_id, workspace_id, request_id,
                       json.dumps(detail) if detail else None))
    except Exception:                                     # noqa: BLE001
        pass


def audit_list(user_id: Optional[int] = None, action: Optional[str] = None, limit: int = 100) -> List[dict]:
    q, a = "SELECT * FROM audit_log WHERE 1=1", []
    if user_id is not None:
        q += " AND user_id=?"; a.append(user_id)
    if action:
        q += " AND action=?"; a.append(action)
    return [dict(r) for r in conn().execute(q + " ORDER BY id DESC LIMIT ?", (*a, limit))]


# ── storage-deletion outbox ──────────────────────────────────────────────
def outbox_add(kind: str, target: str) -> int:
    c = conn()
    with c:
        return c.execute("INSERT INTO storage_deletions(kind,target,created_at) VALUES(?,?,?)", (kind, target, time.time())).lastrowid


def outbox_pending(limit: int = 50) -> List[dict]:
    return [dict(r) for r in conn().execute("SELECT * FROM storage_deletions WHERE done_at IS NULL ORDER BY id LIMIT ?", (limit,))]


def outbox_done(row_id: int) -> None:
    c = conn()
    with c:
        c.execute("UPDATE storage_deletions SET done_at=? WHERE id=?", (time.time(), row_id))


def outbox_failed(row_id: int, error: str) -> None:
    c = conn()
    with c:
        c.execute("UPDATE storage_deletions SET attempts=attempts+1, last_error=? WHERE id=?", (error[:300], row_id))


def outbox_purge(older_than: float) -> int:
    c = conn()
    with c:
        return c.execute("DELETE FROM storage_deletions WHERE done_at IS NOT NULL AND done_at<?", (older_than,)).rowcount


# ── usage (quotas) ───────────────────────────────────────────────────────
def count_lectures(user_id: int) -> int:
    return conn().execute("SELECT COUNT(*) FROM lectures WHERE owner_id=?", (user_id,)).fetchone()[0]


def storage_bytes(user_id: int) -> int:
    return conn().execute("SELECT COALESCE(SUM(size_bytes),0) FROM assets WHERE uploader_id=? AND status!='failed'", (user_id,)).fetchone()[0]


def count_assets(lecture_id: int) -> int:
    return conn().execute("SELECT COUNT(*) FROM assets WHERE lecture_id=? AND status!='failed'", (lecture_id,)).fetchone()[0]


def uploads_since(user_id: int, since: float) -> int:
    return conn().execute("SELECT COUNT(*) FROM assets WHERE uploader_id=? AND created_at>=?", (user_id, since)).fetchone()[0]


def active_jobs_for_user(user_id: int) -> int:
    return conn().execute("SELECT COUNT(*) FROM jobs j JOIN lectures l ON l.id=j.lecture_id WHERE l.owner_id=? "
                          "AND j.status IN ('queued','running','retrying')", (user_id,)).fetchone()[0]


def asks_since(user_id: int, since: float) -> int:
    return conn().execute("SELECT COUNT(*) FROM chat_messages m JOIN chat_conversations v ON v.id=m.conversation_id "
                          "WHERE v.user_id=? AND m.role='user' AND m.created_at>=?", (user_id, since)).fetchone()[0]


# ── stale assets / orphan helpers ────────────────────────────────────────
def stale_pending_assets(before: float) -> List[dict]:
    return [dict(r) for r in conn().execute("SELECT * FROM assets WHERE status IN ('pending','failed') AND created_at<?", (before,))]


def delete_asset_row(asset_id: int) -> None:
    c = conn()
    with c:
        c.execute("DELETE FROM assets WHERE id=?", (asset_id,))


def known_lecture_ids() -> set:
    return {r[0] for r in conn().execute("SELECT id FROM lectures")}


# ── right to erasure ─────────────────────────────────────────────────────
def user_lecture_ids(user_id: int) -> List[int]:
    return [r[0] for r in conn().execute("SELECT id FROM lectures WHERE owner_id=?", (user_id,))]


def purge_user_rows(user_id: int) -> dict:
    """Removes rows that belong to the USER (not to a shared workspace): chat, study sessions/attempts/progress,
    reviews, idempotency keys, membership rows, and personal audit entries' user link. Lectures are deleted
    separately (maintenance.delete_user_data) so storage is cleaned through the outbox."""
    c = conn()
    out = {}
    with c:
        convs = [r[0] for r in c.execute("SELECT id FROM chat_conversations WHERE user_id=?", (user_id,))]
        for cid in convs:
            c.execute("DELETE FROM chat_message_evidence WHERE message_id IN (SELECT id FROM chat_messages WHERE conversation_id=?)", (cid,))
            c.execute("DELETE FROM chat_messages WHERE conversation_id=?", (cid,))
        out["conversations"] = c.execute("DELETE FROM chat_conversations WHERE user_id=?", (user_id,)).rowcount
        sess = [r[0] for r in c.execute("SELECT id FROM study_sessions WHERE user_id=?", (user_id,))]
        for sid in sess:
            c.execute("DELETE FROM study_session_items WHERE session_id=?", (sid,))
        c.execute("DELETE FROM study_attempts WHERE user_id=?", (user_id,))
        c.execute("DELETE FROM study_progress WHERE user_id=?", (user_id,))
        out["sessions"] = c.execute("DELETE FROM study_sessions WHERE user_id=?", (user_id,)).rowcount
        c.execute("DELETE FROM knowledge_reviews WHERE user_id=?", (user_id,))
        from . import notes
        out["notes"] = notes.purge_user(c, user_id)
        c.execute("DELETE FROM idempotency_keys WHERE user_id=?", (user_id,))
        # notebooks (and, crucially, their public share links), sign-in identities and the user row go with the user
        for table in ("notebook_sources", "notebook_items", "notebook_canvas_state", "notebook_shares"):
            c.execute(f"DELETE FROM {table} WHERE owner_id=?", (user_id,))
        out["notebooks"] = c.execute("DELETE FROM notebooks WHERE owner_id=?", (user_id,)).rowcount
        c.execute("DELETE FROM auth_identities WHERE user_id=?", (user_id,))
        c.execute("DELETE FROM users WHERE id=?", (user_id,))
        c.execute("UPDATE audit_log SET user_id=NULL, detail=NULL WHERE user_id=?", (user_id,))
        out["memberships"] = c.execute("DELETE FROM workspace_members WHERE user_id=?", (user_id,)).rowcount
    return out
