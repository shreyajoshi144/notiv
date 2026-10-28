"""Session routes shared by every sign-in method. Sign-in itself is EMAIL + PASSWORD (routes_password_auth.py); the
Google / OpenID Connect flow and its /auth/login + /auth/callback routes were removed.

GET  /auth/config    public: {"password": true, "token_signin": bool}
GET  /auth/me        who the current cookie session / bearer token belongs to
POST /auth/logout    revokes the cookie session server-side and clears its cookies ({"end_session_url": null}, kept for compatibility)
"""
from typing import Optional

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from core.lecture import settings, store
from server.api.deps import get_current_user

router = APIRouter(tags=["auth"])
_NO_STORE = {"Cache-Control": "no-store"}


@router.get("/auth/config", include_in_schema=False)
def auth_config():
    return JSONResponse({"password": True, "token_signin": settings.env_bool("NOTIV_SHOW_TOKEN_SIGNIN", True)}, headers=_NO_STORE)


def _profile(uid: int) -> dict:
    row = store.conn().execute("SELECT email, display_name FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        return {"id": uid, "email": None, "display_name": None, "via": "token"}
    return {"id": uid, "email": row["email"], "display_name": row["display_name"], "via": "password"}


@router.get("/auth/me", include_in_schema=False)
def auth_me(user: dict = Depends(get_current_user)):
    return JSONResponse(_profile(user["id"]), headers=_NO_STORE)


@router.post("/auth/logout", include_in_schema=False)
def auth_logout(request: Request, authorization: Optional[str] = Header(None)):
    """Revoke cookie sessions and clear them; keep legacy bearer logout response compatible."""
    from server import auth_sessions
    has_cookie = bool(request.cookies.get(auth_sessions.COOKIE))
    if has_cookie:
        # Validate even if the session has expired, so an expired browser session can still
        # be revoked and its cookies cleared. Do not let cross-site requests force logout.
        auth_sessions.validate_csrf(request, allow_expired=True)
        auth_sessions.revoke(request)
    else:
        # Stateless bearer tokens remain supported for API clients; they cannot be revoked
        # server-side, so this branch preserves the historical response contract only.
        get_current_user(request, authorization)
    out = JSONResponse({"end_session_url": None}, headers=_NO_STORE)
    if has_cookie:
        auth_sessions.clear(out, request)
    return out
