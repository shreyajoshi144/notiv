"""Stdlib HTTP server for local verification when FastAPI isn't installed.

NOT the production server (that is `uvicorn server.main:app`). It mounts the
very same framework-neutral handlers (server.api.workspace_handlers), the same
bearer-token verification (server.auth_tokens) and the same signed-file check
as the real routes, plus the static workspace UI — so a browser or HTTP client
exercises the real authorization, storage, search and sync logic.

    NOTIV_AUTH_SECRET=<32+ bytes> PYTHONPATH=backend:. python -m server.dev_server 8000
"""
import inspect
import json
import mimetypes
import os
import re
import socket
import sys
import threading
import time
import typing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from core.lecture import authz, config, obs, ops_store, ratelimit, settings
from core.lecture.errors import NotFoundError, RateLimitError
from core.lecture.jobs import JobService
from core.lecture.pipeline import make_job_service
from core.lecture.storage import LocalStorage, content_type_for, disposition_for
from server.api import ask_handlers as A
from server.api import notes_handlers as N
from server.api import study_handlers as S
from server.api import workspace_handlers as H
from server.api import guards
from server.auth_tokens import AuthError, verify_from_env

UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "workspace.html")
COURSE_UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "course.html")
_L = r"^/lectures/(\d+)"
ROUTES = [
    (re.compile(_L + r"/overview$"), H.overview), (re.compile(_L + r"/timeline$"), H.timeline),
    (re.compile(_L + r"/transcript$"), H.transcript), (re.compile(_L + r"/slides$"), H.slides),
    (re.compile(_L + r"/evidence$"), H.evidence), (re.compile(_L + r"/evidence/(\d+)$"), H.evidence_item),
    (re.compile(_L + r"/search$"), H.search), (re.compile(_L + r"/key-moments$"), H.key_moments),
    (re.compile(_L + r"/summary$"), H.summary), (re.compile(_L + r"/position$"), H.position),
    (re.compile(_L + r"/chat$"), A.chat_history), (re.compile(_L + r"/retrieve$"), A.retrieve),
]


_C = r"^/courses/(\d+)"
# (method, pattern, handler, mode)  mode: "q" = query params -> kwargs, "body" = JSON body -> kwargs, "patch" = JSON body -> `patch`
STUDY_ROUTES = [
    ("GET", _L + r"/knowledge$", S.knowledge, "q"), ("GET", _L + r"/concepts$", S.concepts, "q"),
    ("POST", _L + r"/knowledge/regenerate$", S.knowledge_regenerate, "body"),
    ("DELETE", r"^/knowledge/(\d+)$", S.knowledge_delete, "q"), ("POST", r"^/knowledge/(\d+)/review$", S.knowledge_review, "body"),
    ("GET", _L + r"/study/items$", S.study_items, "q"), ("POST", _L + r"/study/generate$", S.study_generate, "body"),
    ("GET", r"^/study/items/(\d+)$", S.item_get, "q"), ("PATCH", r"^/study/items/(\d+)$", S.item_edit, "patch"),
    ("DELETE", r"^/study/items/(\d+)$", S.item_delete, "q"), ("POST", r"^/study/items/(\d+)/regenerate$", S.item_regenerate, "body"),
    ("POST", _L + r"/study/sessions$", S.session_start_lecture, "body"), ("POST", _C + r"/study/sessions$", S.session_start_course, "body"),
    ("GET", r"^/study/sessions/(\d+)$", S.session_get, "q"), ("POST", r"^/study/sessions/(\d+)/attempts$", S.session_attempt, "body"),
    ("POST", r"^/study/sessions/(\d+)/finish$", S.session_finish, "body"),
    ("GET", _L + r"/study/progress$", S.progress_lecture, "q"), ("GET", _C + r"/study/progress$", S.progress_course, "q"),
    ("GET", _C + r"/overview$", S.course_overview, "q"), ("GET", _C + r"/knowledge$", S.course_knowledge, "q"),
    ("GET", _C + r"/concepts$", S.course_concepts, "q"), ("GET", _C + r"/search$", S.course_search, "q"),
    ("GET", _C + r"/study/items$", S.course_study_items, "q"),
]
STUDY_ROUTES += [   # personal notes (Sprint 5 gap closure) — same dispatch mechanism
    ("GET", _L + r"/notes$", N.notes_list, "q"), ("POST", _L + r"/notes$", N.note_create, "body"),
    ("GET", r"^/notes/(\d+)$", N.note_get, "q"), ("PATCH", r"^/notes/(\d+)$", N.note_update, "patch"),
    ("DELETE", r"^/notes/(\d+)$", N.note_delete, "q"),
]
STUDY_ROUTES = [(m, re.compile(p), f, mode) for m, p, f, mode in STUDY_ROUTES]


def _coerce(fn, query: dict) -> dict:
    hints, out = typing.get_type_hints(fn), {}
    for name, vals in query.items():
        if name not in inspect.signature(fn).parameters:
            continue
        t = hints.get(name)
        t = next((a for a in typing.get_args(t) if a is not type(None)), t) if typing.get_origin(t) is typing.Union else t
        try:
            out[name] = (vals[0].lower() in ("1", "true", "yes")) if t is bool else (t(vals[0]) if t in (int, float) else vals[0])
        except ValueError:
            raise H.ApiError(422, f"invalid {name}")
    return out


def _unwrap(t):
    if typing.get_origin(t) is typing.Union:
        args = [a for a in typing.get_args(t) if a is not type(None)]
        return args[0] if len(args) == 1 else None
    return t


def _type_ok(t, v) -> bool:
    t = _unwrap(t)
    if t is None or v is None:
        return True
    if t is bool:
        return isinstance(v, bool)
    if t is int:
        return isinstance(v, int) and not isinstance(v, bool)
    if t is float:
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t is str:
        return isinstance(v, str)
    if typing.get_origin(t) in (list, typing.List):
        a = typing.get_args(t)
        return isinstance(v, list) and all(_type_ok(a[0], x) for x in v) if a else isinstance(v, list)
    return True


def check_body(fn, n_url_args: int, body: dict) -> None:
    """422 (never a 500) for a missing REQUIRED field, an unknown field, or a wrongly-typed field."""
    params = list(inspect.signature(fn).parameters.values())[1:]           # drop user_id
    url_part, rest = params[:n_url_args], params[n_url_args:]
    allowed = {p.name for p in rest}
    unknown = set(body) - allowed
    if unknown:
        raise H.ApiError(422, f"unknown fields: {', '.join(sorted(unknown))}")
    missing = [p.name for p in rest if p.default is inspect.Parameter.empty and p.name not in body]
    if missing:
        raise H.ApiError(422, f"missing fields: {', '.join(missing)}")
    hints = typing.get_type_hints(fn)
    bad = [p.name for p in rest if p.name in body and p.name in hints and not _type_ok(hints[p.name], body[p.name])]
    if bad:
        raise H.ApiError(422, f"invalid field type: {', '.join(bad)}")


class Handler(BaseHTTPRequestHandler):
    server_version = "Notiv"            # no framework/version banner
    sys_version = ""
    log = obs.get_logger("http")

    def log_message(self, *a):                       # we log structured access lines ourselves
        pass

    # ── response helpers (every response carries the request id + security headers) ──
    def _common_headers(self):
        for k, v in guards.response_headers(urlparse(self.path).path).items():
            self.send_header(k, v)
        self.send_header("X-Request-ID", self._rid)

    def _json(self, status, body, headers=None):
        self._status = status
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self._common_headers()
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _ip(self) -> str:
        return guards.client_ip(self.headers.get, self.client_address[0] if self.client_address else None)

    def _user(self):
        if not settings.env("NOTIV_AUTH_SECRET", ""):
            raise H.ApiError(503, "authentication is not configured")
        scheme, _, tok = (self.headers.get("Authorization") or "").partition(" ")
        try:
            if scheme.lower() != "bearer" or not tok.strip():
                raise AuthError("missing")
            return verify_from_env(tok.strip())
        except AuthError as e:
            try:                                      # brute-force / replay throttle, per client IP, failures only
                ratelimit.check("auth_fail", self._ip())
            except RateLimitError as rl:
                raise H.ApiError(rl.http_status, {"message": str(rl), "retry_after": rl.retry_after})
            raise H.ApiError(401, "missing bearer token" if str(e) == "missing" else "invalid or expired token")

    def _limit(self, uid: int, method: str, path: str) -> None:
        try:
            ratelimit.check(guards.policy_for(method, path), uid)
        except RateLimitError as rl:
            raise H.ApiError(rl.http_status, {"message": str(rl), "retry_after": rl.retry_after})

    def do_POST(self):
        self._dispatch("POST")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_GET(self):
        self._dispatch("GET")

    def _body(self) -> dict:
        raw = self.headers.get("Content-Length")
        if raw is None:
            n = 0
        elif not raw.isdigit():
            raise H.ApiError(400, "invalid Content-Length")
        else:
            n = int(raw)
        if n > guards.MAX_JSON_BYTES:
            raise H.ApiError(413, "request too large")
        try:
            b = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, UnicodeDecodeError):
            raise H.ApiError(422, "invalid JSON")
        if not isinstance(b, dict):
            raise H.ApiError(422, "invalid JSON")
        return b

    def _dispatch(self, method):
        self._rid = obs.new_request_id(self.headers.get("X-Request-ID", ""))
        self._status = 500
        tok = obs.request_id_var.set(self._rid)
        t0 = time.perf_counter()
        u = urlparse(self.path)
        try:
            self._route(method, u)
        except H.ApiError as e:
            hdr = {"WWW-Authenticate": "Bearer"} if e.status == 401 else (
                {"Retry-After": str(e.detail["retry_after"])} if e.status in (429, 503) and isinstance(e.detail, dict) and "retry_after" in e.detail else None)
            self._json(e.status, {"detail": e.detail}, hdr)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:                       # noqa: BLE001 — never echo exception text to the client
            obs.log_event(self.log, "unhandled_exception", level=40, path=guards.route_label(u.path),
                          error_class=type(e).__name__, error=str(e))
            obs.inc("notiv_http_unhandled_total", help="unhandled exceptions")
            self._json(500, guards.internal_error_body(self._rid))
        finally:
            lab = guards.route_label(u.path)
            obs.inc("notiv_http_requests_total", help="HTTP requests", method=method, route=lab, status=str(self._status))
            obs.observe("notiv_http_request_seconds", time.perf_counter() - t0, help="HTTP latency", method=method)
            obs.log_event(self.log, "request", method=method, path=lab, status=self._status)
            obs.request_id_var.reset(tok)

    def _route(self, method, u):
        if method == "GET" and (u.path == "/app" or re.match(r"^/app/lectures/\d+$", u.path)):
            return self._file(UI, "text/html")
        if method == "GET" and re.match(r"^/app/courses/\d+$", u.path):
            return self._file(COURSE_UI, "text/html")
        if method == "GET" and u.path == "/healthz":
            return self._json(200, {"status": "ok"})
        if method == "GET" and u.path == "/readyz":
            from core.lecture import health
            r = health.readiness()
            return self._json(200 if r["status"] == "ready" else 503, r)
        if method == "GET" and u.path == "/metrics":
            want = settings.env("NOTIV_METRICS_TOKEN", "")
            got = (self.headers.get("Authorization") or "").partition(" ")[2].strip()
            import hmac
            if not settings.env_bool("NOTIV_METRICS_ENABLED", False) or not want or not hmac.compare_digest(got, want):
                raise H.ApiError(404, "not found")           # indistinguishable from a missing route
            data = obs.render_prometheus().encode()
            self._status = 200
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(data)))
            self._common_headers()
            self.end_headers()
            return self.wfile.write(data)
        if method == "GET" and u.path.startswith("/files/"):
            return self._signed_file(unquote(u.path[len("/files/"):]), parse_qs(u.query))
        m = re.match(r"^/jobs/(\d+)/retry$", u.path)
        if method == "POST" and m:
            uid = self._user()
            self._limit(uid, method, u.path)
            repo = config.get_repo()
            try:
                job = authz.get_job(repo, uid, int(m.group(1)))
            except NotFoundError:
                raise H.ApiError(404, "not found")
            ok = make_job_service(repo, config.get_storage()).retry(int(m.group(1)))
            ops_store.audit(uid, "job.retry", "job", job["id"], job["workspace_id"], self._rid)
            return self._json(200, {"retried": ok})
        for sm, rx, fn, mode in STUDY_ROUTES:
            m = rx.match(u.path) if sm == method else None
            if m:
                uid = self._user()
                self._limit(uid, method, u.path)
                args = [int(g) for g in m.groups()]
                if mode == "q":
                    return self._json(200, fn(uid, *args, **_coerce(fn, parse_qs(u.query))))
                body = self._body()
                if mode == "patch":
                    return self._json(200, fn(uid, *args, body))
                check_body(fn, len(args), body)
                return self._json(200, fn(uid, *args, **body))
        m = re.match(r"^/lectures/(\d+)/chat/(\d+)/general-knowledge$", u.path)
        if m and method == "POST":
            uid = self._user()
            self._limit(uid, method, u.path)
            return self._json(200, A.general_knowledge(uid, int(m.group(1)), int(m.group(2))))
        m = re.match(r"^/lectures/(\d+)/chat$", u.path)
        if m and method == "POST":
            uid = self._user()
            self._limit(uid, method, u.path)
            q = self._body().get("question")
            if not isinstance(q, str) or not q.strip() or len(q) > 500:
                raise H.ApiError(422, "question must be 1-500 characters")
            return self._json(200, A.chat(uid, int(m.group(1)), q))
        if m and method == "DELETE":
            uid = self._user()
            self._limit(uid, method, u.path)
            return self._json(200, A.chat_reset(uid, int(m.group(1))))
        m = re.match(r"^/lectures/(\d+)/ask$", u.path)
        if m and method == "POST":
            uid = self._user()
            self._limit(uid, method, u.path)
            b = self._body()
            q, hist = b.get("question"), b.get("history") or []
            if not isinstance(q, str) or not q.strip() or len(q) > 500:
                raise H.ApiError(422, "question must be 1-500 characters")
            if not isinstance(hist, list) or len(hist) > 50 or not all(isinstance(t, dict) for t in hist):
                raise H.ApiError(422, "history must be a list of at most 50 objects")
            return self._json(200, A.ask_stateless(uid, int(m.group(1)), q, hist))
        if method == "GET":
            for rx, fn in ROUTES:
                m = rx.match(u.path)
                if m:
                    uid = self._user()
                    self._limit(uid, method, u.path)
                    kw = _coerce(fn, parse_qs(u.query))
                    args = [int(g) for g in m.groups()]
                    return self._json(200, fn(uid, *args, **kw))
        raise H.ApiError(404, "not found")

    def _file(self, path, ctype=None, rng=True, disposition=None):
        size = os.path.getsize(path)
        start, end, status = 0, size - 1, 200
        r = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", "")) if rng else None
        if r and (r.group(1) or r.group(2)):
            if r.group(1):
                start = int(r.group(1))
                end = int(r.group(2)) if r.group(2) else size - 1
            else:
                start = max(0, size - int(r.group(2)))
            end = min(end, size - 1)
            if start > end:
                self._status = 416
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self._common_headers()
                self.end_headers()
                return
            status = 206
        self._status = status
        self.send_response(status)
        self.send_header("Content-Type", ctype or mimetypes.guess_type(path)[0] or "application/octet-stream")
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self._common_headers()
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(65536, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def _signed_file(self, key, q):
        st = config.get_storage()
        try:
            ok = isinstance(st, LocalStorage) and st.verify_signed(key, q["exp"][0], q["sig"][0])
        except (KeyError, IndexError):
            ok = False
        if not ok:
            raise H.ApiError(403, "invalid or expired link")
        if not st.exists(key):
            raise H.ApiError(404, "not found")
        with st.local_path(key) as p:
            # server-decided type (never sniffed from content), non-media always downloads
            self._file(p, content_type_for(key), disposition=disposition_for(key))


class DevServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that can be stopped CLEANLY (tests / pytest must exit after reporting):
      * request threads are daemons and are not joined at close (a keep-alive client can no longer block interpreter exit);
      * shutdown() stops the serve loop, closes the listening socket AND force-closes every open client connection;
      * shutdown() is idempotent and safe even if serve_forever never got to run."""
    daemon_threads = True
    block_on_close = False

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._conns, self._conn_lock = set(), threading.Lock()
        self._serving, self._closed = threading.Event(), False

    def serve_forever(self, poll_interval: float = 0.5) -> None:
        self._serving.set()
        try:
            super().serve_forever(poll_interval)
        finally:
            self._serving.clear()

    def get_request(self):
        sock, addr = super().get_request()
        with self._conn_lock:
            self._conns.add(sock)
        return sock, addr

    def shutdown_request(self, request) -> None:
        with self._conn_lock:
            self._conns.discard(request)
        super().shutdown_request(request)

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._serving.wait(2.0):                     # a serve loop is (about to be) running: ask it to stop and wait
            super().shutdown()
        self.server_close()
        with self._conn_lock:
            conns, self._conns = list(self._conns), set()
        for c in conns:                                 # unblock handler threads parked on idle keep-alive sockets
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                c.close()
            except OSError:
                pass


def make_server(port: int = 8000, host: str = "127.0.0.1") -> DevServer:
    return DevServer((host, port), Handler)


if __name__ == "__main__":
    srv = make_server(int(sys.argv[1]) if len(sys.argv) > 1 else 8000)
    print(f"dev server on http://127.0.0.1:{srv.server_address[1]}  (UI: /app/lectures/<id>)")
    srv.serve_forever()
