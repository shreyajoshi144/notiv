"""Lecture-workspace endpoint logic, independent of any web framework.

Each handler takes the already-authenticated `user_id` plus typed parameters,
calls WorkspaceService (which authorizes), and returns JSON-able data or
raises ApiError(status, detail). routes_workspace.py exposes them through
FastAPI; the same functions are what the tests and the stdlib dev server call.

Error contract (all JSON `{"detail": ...}`):
  404  not found / not yours (indistinguishable)
  409  lecture exists but isn't ready: detail = {message, state, stage, progress_pct, error, can_retry, ...}
  400  bad input (empty query, bad slide number, ...)
  409  also: state conflict, detail = {message, code}  (e.g. already_answered)
  429  rate limited: detail = {message, retry_after}
"""
from typing import Optional

from core.lecture import config
from core.lecture.errors import (ConflictError, NotFoundError, NotReadyError, QuotaError, RateLimitError,
                                 ValidationError)
from core.lecture.workspace import WorkspaceService


class ApiError(Exception):
    def __init__(self, status: int, detail):
        super().__init__(str(detail))
        self.status, self.detail = status, detail


def _svc() -> WorkspaceService:
    return WorkspaceService(config.get_repo(), config.get_storage())


def _call(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except (NotFoundError, OverflowError):
        # OverflowError: a client-supplied id beyond 64-bit (e.g. /lectures/99999999999999999999) can never exist.
        # It used to surface as an HTTP 500 from the SQLite driver; it is now the same 404 as any unknown id.
        raise ApiError(404, "not found")
    except QuotaError as e:
        raise ApiError(413 if e.quota == "storage" else 429, {"message": str(e), "code": "quota_exceeded", "quota": e.quota})
    except NotReadyError as e:
        raise ApiError(409, {"message": str(e), **e.info})
    except ValidationError as e:
        raise ApiError(400, str(e))
    except ConflictError as e:
        raise ApiError(409, {"message": str(e), "code": e.code})
    except RateLimitError as e:
        raise ApiError(e.http_status, {"message": str(e), "retry_after": e.retry_after})


def overview(user_id: int, lecture_id: int):
    return _call(_svc().overview, user_id, lecture_id)


def timeline(user_id: int, lecture_id: int):
    return _call(_svc().timeline, user_id, lecture_id)


def transcript(user_id: int, lecture_id: int, start: Optional[float] = None, end: Optional[float] = None,
               slide: Optional[int] = None, after_id: int = 0, limit: int = 500):
    return _call(_svc().transcript, user_id, lecture_id, start, end, slide, after_id, limit)


def slides(user_id: int, lecture_id: int):
    return _call(_svc().slides, user_id, lecture_id)


def evidence(user_id: int, lecture_id: int, source_type: Optional[str] = None, slide: Optional[int] = None,
             start: Optional[float] = None, end: Optional[float] = None, after_id: int = 0, limit: int = 100):
    return _call(_svc().evidence, user_id, lecture_id, source_type, slide, start, end, after_id, limit)


def evidence_item(user_id: int, lecture_id: int, evidence_id: int):
    return _call(_svc().evidence_item, user_id, lecture_id, evidence_id)


def search(user_id: int, lecture_id: int, q: str = "", limit: int = 20, source_type: Optional[str] = None):
    return _call(_svc().search, user_id, lecture_id, q, limit, source_type)


def key_moments(user_id: int, lecture_id: int):
    return _call(_svc().key_moments, user_id, lecture_id)


def summary(user_id: int, lecture_id: int):
    return _call(_svc().summary, user_id, lecture_id)


def mind_map(user_id: int, lecture_id: int):
    return _call(_svc().mind_map, user_id, lecture_id)


def ensure_ai_study(user_id: int, lecture_id: int, max_wait: float = 120.0) -> dict:
    """Make sure AI study material exists for an AUTHORIZED, ready lecture (generates it when missing or stale). Never
    raises for provider problems: the result says what happened, with a secret-free reason."""
    from core.lecture import ai_study
    svc = _svc()
    _call(svc._ready, user_id, lecture_id)
    return ai_study.ensure(svc.repo, lecture_id, max_wait=max_wait)


def position(user_id: int, lecture_id: int, t: Optional[float] = None, slide: Optional[int] = None,
             segment: Optional[int] = None, evidence: Optional[int] = None):
    return _call(_svc().locate, user_id, lecture_id, t, slide, segment, evidence)
