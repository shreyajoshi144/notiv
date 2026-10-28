"""Opaque, revocable browser sessions backed by the existing Notiv database."""
import hashlib, secrets, time
from fastapi import HTTPException, Request
from core.lecture import store, settings

COOKIE = "notiv_session"
CSRF_COOKIE = "notiv_csrf"
TTL = settings.env_int("NOTIV_SESSION_TTL_SECONDS", 3600, lo=300, hi=86400)

def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()

def create(user_id: int, response, request: Request):
    sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    now = time.time()
    c = store.conn()
    c.execute("INSERT INTO auth_sessions(id,user_id,expires_at,created_at,csrf_hash) VALUES(?,?,?,?,?)", (digest(sid), user_id, now+TTL, now, digest(csrf)))
    c.commit()
    secure = request.url.scheme == "https" or settings.is_production()
    response.set_cookie(COOKIE, sid, max_age=TTL, httponly=True, secure=secure, samesite="lax", path="/")
    response.set_cookie(CSRF_COOKIE, csrf, max_age=TTL, httponly=False, secure=secure, samesite="lax", path="/")

def resolve(request: Request):
    sid = request.cookies.get(COOKIE, "")
    if not sid or len(sid) > 200: return None
    row = store.conn().execute("SELECT user_id,expires_at,revoked_at,csrf_hash FROM auth_sessions WHERE id=?", (digest(sid),)).fetchone()
    if not row or row["revoked_at"] is not None or row["expires_at"] <= time.time(): return None
    if request.method.upper() not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        expected = str(request.base_url).rstrip("/")
        if origin and origin.rstrip("/") != expected:
            raise HTTPException(403, "origin check failed")
        csrf = request.headers.get("x-csrf-token", "")
        cookie_csrf = request.cookies.get(CSRF_COOKIE, "")
        if not csrf or not cookie_csrf or not secrets.compare_digest(csrf, cookie_csrf) or not secrets.compare_digest(digest(csrf), row["csrf_hash"]):
            raise HTTPException(403, "CSRF validation failed")
    return {"id": int(row["user_id"]), "session_id": digest(sid)}

def validate_csrf(request: Request, *, allow_expired: bool = False) -> bool:
    """Validate the double-submit CSRF token against the server-side session record.

    Logout may use an expired session cookie so the server can still revoke it and clear
    the browser cookies. All other mutation handlers use resolve(), which rejects expiry.
    """
    sid = request.cookies.get(COOKIE, "")
    if not sid or len(sid) > 200:
        return False
    origin = request.headers.get("origin")
    expected = str(request.base_url).rstrip("/")
    if origin and origin.rstrip("/") != expected:
        raise HTTPException(403, "origin check failed")
    csrf = request.headers.get("x-csrf-token", "")
    cookie_csrf = request.cookies.get(CSRF_COOKIE, "")
    if not csrf or not cookie_csrf or not secrets.compare_digest(csrf, cookie_csrf):
        raise HTTPException(403, "CSRF validation failed")
    row = store.conn().execute("SELECT csrf_hash, revoked_at, expires_at FROM auth_sessions WHERE id=?", (digest(sid),)).fetchone()
    if not row or not secrets.compare_digest(digest(csrf), row["csrf_hash"]):
        raise HTTPException(403, "CSRF validation failed")
    if row["revoked_at"] is not None and not allow_expired:
        raise HTTPException(403, "CSRF validation failed")
    if not allow_expired and row["expires_at"] <= time.time():
        raise HTTPException(403, "CSRF validation failed")
    return True

def revoke(request: Request):
    sid = request.cookies.get(COOKIE, "")
    if sid:
        c = store.conn(); c.execute("UPDATE auth_sessions SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (time.time(), digest(sid))); c.commit()

def clear(response, request: Request | None = None):
    secure = bool(request and (request.url.scheme == "https" or settings.is_production()))
    response.delete_cookie(COOKIE, path="/", secure=secure, httponly=True, samesite="lax")
    response.delete_cookie(CSRF_COOKIE, path="/", secure=secure, httponly=False, samesite="lax")
