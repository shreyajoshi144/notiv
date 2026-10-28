"""HTTP-edge hardening shared by the FastAPI app and the stdlib dev server. NO web-framework imports.

* HardeningMiddleware (pure ASGI): request id, body-size limits enforced BEFORE/WHILE the body is read, security
  headers, access log (path only — the query string can carry signed-URL secrets), metrics, generic 500s that never
  echo exception text.
* policy_for / client_ip / error helpers used by deps and the dev server.
"""
import json
import re
import time
from typing import Callable, Dict, Optional

from core.lecture import obs, settings, validation

log = obs.get_logger("http")
MAX_JSON_BYTES = settings.env_int("NOTIV_MAX_JSON_BYTES", 65536, lo=1024)
_UPLOAD_PATH = re.compile(r"^/lectures/\d+/assets/?$")
_ID = re.compile(r"/\d+")

SECURITY_HEADERS: Dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": "frame-ancestors 'none'",
}


# HTML pages: no inline script at all (script-src 'self'), no framing, no third-party origins except https media/images
# (S3 pre-signed URLs). style-src keeps 'unsafe-inline' only because the UI sets dynamic widths/positions via style attributes.
PAGE_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; "
            "media-src 'self' blob: https:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'")
_PAGE_PATH = re.compile(r"^(/|/sign-in|/app(/.*)?|/share/[^/]+)$")


def response_headers(path: str) -> Dict[str, str]:
    h = dict(SECURITY_HEADERS)
    if settings.env_bool("NOTIV_HSTS", False):
        h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if _PAGE_PATH.match(path):
        h["Content-Security-Policy"] = PAGE_CSP
    if path.startswith("/static/"):
        h["Cache-Control"] = "no-cache"                 # revalidated with the ETag StaticFiles sends: fast, never stale
    elif not path.startswith(("/files/", "/app")):
        h["Cache-Control"] = "no-store"
    return h


def route_label(path: str) -> str:
    """Low-cardinality path for metrics/logs: numeric ids collapsed, and capped."""
    return _ID.sub("/{id}", path.split("?")[0])[:80]


def body_limit(method: str, path: str) -> int:
    if method in ("GET", "HEAD", "OPTIONS", "DELETE"):
        return 0 if method != "DELETE" else MAX_JSON_BYTES
    if _UPLOAD_PATH.match(path):
        return validation.MAX_BYTES + 1024 * 1024          # multipart framing overhead
    return MAX_JSON_BYTES


def policy_for(method: str, path: str) -> str:
    """Which rate-limit policy a request falls under."""
    if method == "POST" and _UPLOAD_PATH.match(path):
        return "upload"
    if method == "POST" and (re.search(r"/process$", path) or re.search(r"^/jobs/\d+/(retry|cancel)$", path)):
        return "process"
    if path.endswith(("/chat", "/ask")) or "/retrieve" in path:
        return "ask"
    if "/study/" in path or "/knowledge" in path:
        return "study" if method != "GET" else "read"
    return "read" if method in ("GET", "HEAD") else "write"


def client_ip(headers_get: Callable[[str], Optional[str]], peer: Optional[str]) -> str:
    """The direct peer, unless NOTIV_TRUST_PROXY=1 (then the LEFT-most X-Forwarded-For entry, which the trusted proxy
    set). Never trust X-Forwarded-For from the open internet: it would let a client pick its own rate-limit bucket."""
    if settings.env_bool("NOTIV_TRUST_PROXY", False):
        xff = (headers_get("x-forwarded-for") or "").split(",")[0].strip()
        if xff:
            return xff[:64]
    return (peer or "unknown")[:64]


def error_body(detail, request_id: str = "") -> dict:
    # exactly {"detail": ...}: the id travels in the X-Request-ID header so "not yours" and "missing" bodies stay identical
    return {"detail": detail}


def internal_error_body(request_id: str = "") -> dict:
    # EXACTLY {"detail": "internal server error"}; the request id is only in the X-Request-ID response header
    return {"detail": "internal server error"}


class _TooLarge(Exception):
    pass


class HardeningMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        hdrs = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        rid = obs.new_request_id(hdrs.get("x-request-id", ""))
        tok = obs.request_id_var.set(rid)
        method, path = scope["method"], scope["path"]
        limit = body_limit(method, path)
        t0, status, started = time.perf_counter(), 500, False
        extra = response_headers(path)

        async def send_wrap(msg):
            nonlocal status, started
            if msg["type"] == "http.response.start":
                started, status = True, msg["status"]
                keep = [(k, v) for k, v in msg.get("headers", []) if k.decode("latin-1").lower() not in
                        {h.lower() for h in extra} | {"x-request-id", "server"}]
                keep += [(k.encode(), v.encode()) for k, v in extra.items()] + [(b"x-request-id", rid.encode())]
                msg = {**msg, "headers": keep}
            await send(msg)

        async def json_response(code: int, body: dict):
            data = json.dumps(body).encode()
            await send_wrap({"type": "http.response.start", "status": code,
                             "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode())]})
            await send_wrap({"type": "http.response.body", "body": data})

        try:
            cl = hdrs.get("content-length")
            if cl is not None:
                if not cl.isdigit():
                    return await json_response(400, error_body("invalid Content-Length", rid))
                if int(cl) > limit and method not in ("GET", "HEAD", "OPTIONS"):
                    return await json_response(413, error_body("request body too large", rid))
            seen = 0

            async def receive_wrap():
                nonlocal seen
                m = await receive()
                if m["type"] == "http.request":
                    seen += len(m.get("body", b""))
                    if method not in ("GET", "HEAD", "OPTIONS") and seen > limit:       # chunked / lying Content-Length
                        raise _TooLarge()
                return m

            await self.app(scope, receive_wrap, send_wrap)
        except _TooLarge:
            if not started:
                await json_response(413, error_body("request body too large", rid))
        except Exception as e:                   # noqa: BLE001 — last-resort: never leak internals
            obs.log_event(log, "unhandled_exception", level=40, path=path, error_class=type(e).__name__, error=str(e))
            obs.inc("notiv_http_unhandled_total", help="unhandled exceptions")
            if not started:
                await json_response(500, internal_error_body(rid))
        finally:
            dur = time.perf_counter() - t0
            lab = route_label(path)
            obs.inc("notiv_http_requests_total", help="HTTP requests", method=method, route=lab, status=str(status))
            obs.observe("notiv_http_request_seconds", dur, help="HTTP latency", method=method)
            obs.log_event(log, "request", method=method, path=lab, status=status, ms=round(dur * 1000, 1))
            obs.request_id_var.reset(tok)
