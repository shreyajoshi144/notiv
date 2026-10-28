"""FastAPI application entrypoint for the lecture API.

    PYTHONPATH=backend:. uvicorn server.main:app

Mounts the lecture router only. The legacy meeting/chat router (server.api.routes_chat) is deliberately NOT mounted:
it depends on modules that are absent from this checkout and must not be able to break the lecture API at import time.

Production (NOTIV_ENV=production): refuses to start on weak/missing secrets, SQLite or local storage (see
core.lecture.settings); OpenAPI docs are off; /metrics needs NOTIV_METRICS_ENABLED=1 + NOTIV_METRICS_TOKEN.
Processing: run `python -m core.lecture.worker` as its own process (the API also starts an in-process worker on the first
/lectures/{id}/process request unless NOTIV_INPROCESS_WORKER=0).

UNVERIFIED in the authoring sandbox (FastAPI not installable). The hardening middleware itself is pure ASGI and IS
covered by core/test_sprint5_ops.py::TestAsgiMiddleware; the wiring below is covered by core/test_sprint5_fastapi.py,
which SKIPS when FastAPI/httpx are missing.
"""
import hmac
import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from core.lecture import health, obs, settings
from core.lecture.errors import NotivError
from server.api import routes_ai_quiz, routes_question_bank
from server.api import routes_ask, routes_auth, routes_password_auth, routes_lectures, routes_notes, routes_study, routes_workspace, routes_notebooks
from server.api.deps import http_error
from server.api.guards import HardeningMiddleware, error_body

obs.setup_logging()
_docs = settings.env_bool("NOTIV_DOCS_ENABLED", False) and not settings.is_production()
app = FastAPI(title="Notiv lecture API", docs_url="/docs" if _docs else None, redoc_url=None,
              openapi_url="/openapi.json" if _docs else None)

_origins = [o.strip() for o in settings.env("NOTIV_CORS_ORIGINS", "").split(",") if o.strip() and o.strip() != "*"]
if _origins:
    app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"], allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"])
app.add_middleware(HardeningMiddleware)          # added last = outermost user middleware: sees every request


@app.on_event("startup")
def _startup() -> None:
    settings.validate_or_raise("api")             # production: fail closed on unsafe configuration


@app.exception_handler(NotivError)
async def _domain_error(request: Request, exc: NotivError):
    e = http_error(exc)
    return JSONResponse(error_body(e.detail, obs.request_id_var.get()), status_code=e.status_code, headers=e.headers)


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    # field locations + messages only: never echo the submitted values (they may be secrets / large)
    errs = [{"loc": [str(x) for x in e.get("loc", [])], "msg": e.get("msg", "invalid")} for e in exc.errors()][:20]
    return JSONResponse(error_body(errs, obs.request_id_var.get()), status_code=422)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    r = health.readiness()
    return JSONResponse(r, status_code=200 if r["status"] == "ready" else 503)


@app.get("/metrics", include_in_schema=False)
def metrics(request: Request):
    want = settings.env("NOTIV_METRICS_TOKEN", "")
    got = (request.headers.get("authorization") or "").partition(" ")[2].strip()
    if not settings.env_bool("NOTIV_METRICS_ENABLED", False) or not want or not hmac.compare_digest(got, want):
        return JSONResponse({"detail": "not found"}, status_code=404)
    return PlainTextResponse(obs.render_prometheus(), media_type="text/plain; version=0.0.4")


app.include_router(routes_lectures.router)
app.include_router(routes_workspace.router)
app.include_router(routes_ask.router)
app.include_router(routes_study.router)
app.include_router(routes_notes.router)
app.include_router(routes_notebooks.router)
app.include_router(routes_auth.router)
app.include_router(routes_password_auth.router)
app.include_router(routes_ai_quiz.router)
app.include_router(routes_question_bank.router)

_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_UI, _COURSE_UI = os.path.join(_STATIC, "workspace.html"), os.path.join(_STATIC, "course.html")
_LIBRARY_UI = os.path.join(_STATIC, "library.html")
_NOTEBOOK_UI = os.path.join(_STATIC, "notebooks.html")
_SHARED_UI = os.path.join(_STATIC, "shared-notebook.html")
_LANDING, _SIGN_IN = os.path.join(_STATIC, "index.html"), os.path.join(_STATIC, "sign-in.html")
_AUTH_UI = os.path.join(_STATIC, "auth.html")
app.mount("/static", StaticFiles(directory=_STATIC), name="static")

@app.get("/", include_in_schema=False)
def landing_ui() -> FileResponse:
    return FileResponse(_LANDING, media_type="text/html", headers={"Cache-Control": "no-cache"})

@app.get("/sign-in", include_in_schema=False)
def sign_in_ui() -> FileResponse:
    return FileResponse(_AUTH_UI, media_type="text/html", headers={"Cache-Control": "no-cache"})


@app.get("/register", include_in_schema=False)
def register_ui() -> FileResponse:
    return FileResponse(_AUTH_UI, media_type="text/html", headers={"Cache-Control":"no-cache"})

@app.get("/forgot-password", include_in_schema=False)
def forgot_password_ui() -> FileResponse:
    return FileResponse(_AUTH_UI, media_type="text/html", headers={"Cache-Control":"no-cache"})

@app.get("/reset-password", include_in_schema=False)
def reset_password_ui() -> FileResponse:
    return FileResponse(_AUTH_UI, media_type="text/html", headers={"Cache-Control":"no-cache"})

@app.get("/app", include_in_schema=False)
def library_ui() -> FileResponse:
    """Authenticated lecture-library home; all content is loaded from user-scoped APIs."""
    return FileResponse(_LIBRARY_UI, media_type="text/html", headers={"Cache-Control": "no-cache"})


@app.get("/app/notebooks", include_in_schema=False)
def notebooks_ui() -> FileResponse:
    return FileResponse(_NOTEBOOK_UI, media_type="text/html", headers={"Cache-Control": "no-cache"})

@app.get("/app/notebooks/{notebook_id}", include_in_schema=False)
def notebook_ui(notebook_id: int) -> FileResponse:
    return FileResponse(_NOTEBOOK_UI, media_type="text/html", headers={"Cache-Control": "no-cache"})


@app.get("/share/{token}", include_in_schema=False)
def shared_notebook_ui(token: str) -> FileResponse:
    return FileResponse(_SHARED_UI, media_type="text/html", headers={"Cache-Control": "no-store"})

@app.get("/app/lectures/{lecture_id}", include_in_schema=False)
def workspace_ui(lecture_id: int) -> FileResponse:
    """Lecture viewer (authenticates API calls with a provisioned bearer token)."""
    return FileResponse(_UI, media_type="text/html", headers={"Cache-Control": "no-cache"})


@app.get("/app/courses/{course_id}", include_in_schema=False)
def course_ui(course_id: int) -> FileResponse:
    """Course page: search and review knowledge across the lectures of one course (static; bearer-token API calls)."""
    return FileResponse(_COURSE_UI, media_type="text/html", headers={"Cache-Control": "no-cache"})
