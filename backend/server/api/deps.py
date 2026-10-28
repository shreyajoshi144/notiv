"""FastAPI dependencies shared by the API routers.

Deliberately imports nothing from the legacy meeting/chat stack (routes_chat, meeting_service, rag_service,
core.rag_engine, ...), so the lecture API can be imported and served without it.

UNVERIFIED in the authoring sandbox: FastAPI is not installed there. core/test_sprint5_fastapi.py runs these paths when
FastAPI + httpx are available (CI / your machine) and SKIPS otherwise.
"""
from typing import Optional

from fastapi import Header, HTTPException, Request, status

from core.lecture import ratelimit, settings
from core.lecture.errors import (ConflictError, NotFoundError, NotReadyError, NotivError, QuotaError, RateLimitError,
                                 ValidationError)
from server.api.guards import client_ip, policy_for
from server.auth_tokens import AuthError, verify_from_env

_UNAUTH = {"WWW-Authenticate": "Bearer"}


def http_error(e: Exception) -> HTTPException:
    """Domain error -> HTTP error with the same contract as the stdlib dev server / workspace_handlers."""
    if isinstance(e, (NotFoundError, OverflowError)):
        return HTTPException(404, "not found")
    if isinstance(e, NotReadyError):
        return HTTPException(409, {"message": str(e), **e.info})
    if isinstance(e, ConflictError):
        return HTTPException(409, {"message": str(e), "code": e.code})
    if isinstance(e, QuotaError):
        return HTTPException(413 if e.quota == "storage" else 429, {"message": str(e), "code": "quota_exceeded", "quota": e.quota})
    if isinstance(e, RateLimitError):
        return HTTPException(e.http_status, {"message": str(e), "retry_after": e.retry_after}, headers={"Retry-After": str(e.retry_after)})
    if isinstance(e, ValidationError):
        return HTTPException(400, str(e))
    return HTTPException(500, "internal server error")


def get_current_user(request: Request, authorization: Optional[str] = Header(None)) -> dict:
    """Resolves the caller from `Authorization: Bearer <token>`, then applies the per-user rate limit.

    Fails closed: no secret configured -> 503 (never an anonymous/default user); missing/invalid/expired token -> 401
    (and repeated failures from one IP -> 429). Returns {"id": <int>}.
    Configure with NOTIV_AUTH_SECRET (>= 32 bytes; or NOTIV_AUTH_SECRET_FILE), optional NOTIV_AUTH_SECRET_PREVIOUS
    (rotation), NOTIV_AUTH_ISSUER / NOTIV_AUTH_AUDIENCE / NOTIV_AUTH_MAX_TTL_SECONDS — or replace this function with
    your IdP/session lookup.
    """
    # Browser sessions are opaque, server-side, revocable and CSRF-protected.
    if getattr(request, "cookies", {}).get("notiv_session"):
        from server.auth_sessions import resolve
        session_user = resolve(request)
        if not session_user:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session expired", headers=_UNAUTH)
        uid = session_user["id"]
        try:
            ratelimit.check(policy_for(request.method, request.url.path), uid)
        except RateLimitError as rl:
            raise http_error(rl)
        return {"id": uid}
    if not settings.env("NOTIV_AUTH_SECRET", ""):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "authentication is not configured")
    scheme, _, token = (authorization or "").partition(" ")
    try:
        if scheme.lower() != "bearer" or not token.strip():
            raise AuthError("missing")
        uid = verify_from_env(token.strip())
    except AuthError as e:
        if "not configured" in str(e):
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "authentication is not configured")
        try:
            ratelimit.check("auth_fail", client_ip(request.headers.get, request.client.host if request.client else None))
        except RateLimitError as rl:
            raise http_error(rl)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token" if str(e) == "missing" else "invalid or expired token",
                            headers=_UNAUTH)
    try:
        ratelimit.check(policy_for(request.method, request.url.path), uid)
    except RateLimitError as rl:
        raise http_error(rl)
    return {"id": uid}
