"""Courses, lectures, assets, jobs, viewer data, search, evidence, corrections, Ask.

Thin HTTP layer: every handler resolves the caller (`get_current_user`, the
same dependency routes_chat.py uses — NOT present in this checkout), then
calls services that enforce workspace-scoped authorization (core.lecture.authz).
NotFoundError -> 404 for both "missing" and "not yours".

Register:  app.include_router(routes_lectures.router)

NOTE: written against FastAPI but NOT executed in the authoring sandbox (FastAPI isn't installable there) =>
UNVERIFIED. The services underneath are covered by core/test_foundation.py, core/test_lecture_pipeline.py and
core/test_sprint5_ops.py; core/test_sprint5_fastapi.py exercises these routes when FastAPI is available.

Sprint 5: Idempotency-Key on create/process, quotas, audit log, per-route validation limits, domain-error mapping
(deps.http_error), hardened /files route (server-decided content type, nosniff, defensive signature check).
"""
import os
import shutil
import tempfile
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import server.api.ask_handlers as ask_handlers
from server.api.deps import get_current_user, http_error
from core.lecture import authz, config, corrections, idempotency, obs, ops_store, quotas, settings, validation, worker
from core.lecture.assets import AssetService
from core.lecture.errors import NotivError, NotFoundError, ValidationError
from core.lecture.storage import LocalStorage, content_type_for, disposition_for
from core.lecture.evidence import EvidenceService
from core.lecture.jobs import JobService
from core.lecture.pipeline import make_job_service
from core.lecture.service import LectureService
from core.lecture.storage import LocalStorage

router = APIRouter(tags=["lectures"])


def _svc():
    repo, storage = config.get_repo(), config.get_storage()
    return repo, storage, LectureService(repo), AssetService(repo, storage)


def _jobs() -> JobService:
    return make_job_service(config.get_repo(), config.get_storage())


def _nf(e: NotFoundError):
    return HTTPException(status.HTTP_404_NOT_FOUND, "not found")


def _idem(user: dict, scope: str, key, payload, fn, response):
    """Runs fn() -> (status, body) under an Idempotency-Key (if given). Domain errors become HTTP errors."""
    try:
        code, body, replayed = idempotency.run(user["id"], scope, key, payload, fn)
    except NotivError as e:
        raise http_error(e)
    response.status_code = code
    if replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return body


def _ready(lec: dict):
    if lec["status"] != "done":
        raise HTTPException(status.HTTP_409_CONFLICT, f"lecture is {lec['status']}")


# ── workspaces / courses / lectures ──────────────────────────────────────
_ID = dict(ge=1, le=2 ** 63 - 1)


@router.get("/courses")
def list_courses(limit: int = 100, offset: int = 0, user: dict = Depends(get_current_user)):
    """Return only courses in workspaces the authenticated user can access."""
    from fastapi import Query
    if limit < 1 or limit > 200 or offset < 0:
        raise HTTPException(422, "limit must be 1..200 and offset must be non-negative")
    repo, _, _, _ = _svc()
    return {"items": repo.list_courses_for_user(user["id"], limit, offset), "limit": limit, "offset": offset}


@router.get("/lectures")
def list_lectures(limit: int = 100, offset: int = 0, course_id: Optional[int] = None,
                  user: dict = Depends(get_current_user)):
    """Return real lecture metadata, filtered by workspace membership and optionally course."""
    if limit < 1 or limit > 200 or offset < 0:
        raise HTTPException(422, "limit must be 1..200 and offset must be non-negative")
    repo, _, _, _ = _svc()
    if course_id is not None:
        try:
            authz.get_course(repo, user["id"], course_id)
        except NotFoundError as e:
            raise _nf(e)
    return {"items": repo.list_lectures_for_user(user["id"], limit, offset, course_id),
            "limit": limit, "offset": offset}


class CourseIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    workspace_id: Optional[int] = Field(None, **_ID)


class LectureIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    course_id: Optional[int] = Field(None, **_ID)
    workspace_id: Optional[int] = Field(None, **_ID)
    language: Optional[str] = Field(None, pattern=r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$", max_length=16)


@router.post("/courses", status_code=status.HTTP_201_CREATED)
def create_course(body: CourseIn, response: Response, user: dict = Depends(get_current_user),
                  idempotency_key: Optional[str] = Header(None)):
    repo, _, lectures, _ = _svc()

    def work():
        name = body.name.strip()
        if not name:
            raise ValidationError("course name required")
        cid = lectures.create_course(user["id"], name, body.workspace_id)
        ops_store.audit(user["id"], "course.create", "course", cid)
        return 201, {"course_id": cid}
    return _idem(user, "course.create", idempotency_key, body.model_dump(), work, response)


@router.post("/lectures", status_code=status.HTTP_201_CREATED)
def create_lecture(body: LectureIn, response: Response, user: dict = Depends(get_current_user),
                   idempotency_key: Optional[str] = Header(None)):
    """Creates an empty lecture; add assets, then POST /lectures/{id}/process. Send `Idempotency-Key` to make retries safe."""
    _, _, lectures, _ = _svc()

    def work():
        quotas.check_create_lecture(user["id"])
        lid = lectures.create(user["id"], body.title.strip(), body.course_id, body.workspace_id, body.language)
        ops_store.audit(user["id"], "lecture.create", "lecture", lid)
        return 201, {"lecture_id": lid}
    return _idem(user, "lecture.create", idempotency_key, body.model_dump(), work, response)


@router.get("/upload-formats")
def upload_formats():
    """The ONLY formats Notiv accepts (PDF, PPTX, MP3, MP4) plus the size/page/duration limits, for UIs and API clients."""
    return validation.supported_formats()


@router.post("/lectures/{lecture_id}/assets", status_code=status.HTTP_201_CREATED)
def upload_asset(lecture_id: int, file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    """Streams to a temp file under the size cap, then validates CONTENT
    (signature / ffprobe / zip structure / duration) before anything is stored."""
    _, _, _, assets = _svc()
    try:
        authz.get_lecture(config.get_repo(), user["id"], lecture_id)           # fail fast, before reading the body
    except NotFoundError as e:
        raise _nf(e)
    tmpdir = tempfile.mkdtemp(prefix="notiv-up-")
    try:
        path, size = os.path.join(tmpdir, "upload.bin"), 0
        with open(path, "wb") as f:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > validation.MAX_BYTES:
                    raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "file too large")
                f.write(chunk)
        try:
            a = assets.add_from_file(user["id"], lecture_id, path, file.filename or "", file.content_type or "")
        except ValidationError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
        except NotivError as e:                                   # quota (429/413), not found, ...
            raise http_error(e)
        return {k: a[k] for k in ("id", "type", "original_filename", "mime_type", "size_bytes", "checksum",
                                  "duration_seconds", "status")}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@router.get("/lectures/{lecture_id}/assets")
def list_assets(lecture_id: int, user: dict = Depends(get_current_user)):
    _, _, _, assets = _svc()
    try:
        return [{k: a[k] for k in ("id", "type", "original_filename", "mime_type", "size_bytes", "duration_seconds",
                                   "status")} for a in assets.list(user["id"], lecture_id)]
    except NotFoundError as e:
        raise _nf(e)


@router.post("/lectures/{lecture_id}/process", status_code=status.HTTP_202_ACCEPTED)
def process(lecture_id: int, response: Response, user: dict = Depends(get_current_user),
            idempotency_key: Optional[str] = Header(None)):
    """Enqueue processing. Idempotent by nature (one active job per lecture): a repeat returns the same job."""
    repo, _, lectures, _ = _svc()

    def work():
        authz.get_lecture(repo, user["id"], lecture_id)
        if repo.active_job(lecture_id, "process_lecture") is None:
            quotas.check_process(user["id"])
        jid = lectures.request_processing(user["id"], lecture_id, _jobs())
        ops_store.audit(user["id"], "lecture.process", "job", jid, detail={"lecture_id": lecture_id})
        return 202, {"job_id": jid, "status": "queued"}
    body = _idem(user, f"lecture.process:{lecture_id}", idempotency_key, {"lecture_id": lecture_id}, work, response)
    if settings.env_bool("NOTIV_INPROCESS_WORKER", True):
        worker.start()
        worker.wake()
    return body


@router.get("/lectures/{lecture_id}")
def get_status(lecture_id: int, user: dict = Depends(get_current_user)):
    _, _, lectures, _ = _svc()
    try:
        return lectures.status(user["id"], lecture_id)
    except NotFoundError as e:
        raise _nf(e)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int, user: dict = Depends(get_current_user)):
    try:
        authz.get_job(config.get_repo(), user["id"], job_id)
    except NotFoundError as e:
        raise _nf(e)
    ok = _jobs().cancel(job_id)
    ops_store.audit(user["id"], "job.cancel", "job", job_id)
    return {"cancelled": ok}


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: int, user: dict = Depends(get_current_user)):
    try:
        authz.get_job(config.get_repo(), user["id"], job_id)
    except NotFoundError as e:
        raise _nf(e)
    ok = _jobs().retry(job_id)
    ops_store.audit(user["id"], "job.retry", "job", job_id)
    if ok and settings.env_bool("NOTIV_INPROCESS_WORKER", True):
        worker.start(); worker.wake()
    return {"retried": ok}


@router.delete("/lectures/{lecture_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lecture(lecture_id: int, user: dict = Depends(get_current_user)):
    _, _, _, assets = _svc()
    try:
        assets.delete_lecture(user["id"], lecture_id)
    except NotFoundError as e:
        raise _nf(e)


# ── viewer / search / evidence ───────────────────────────────────────────
@router.get("/lectures/{lecture_id}/viewer")
def viewer(lecture_id: int, user: dict = Depends(get_current_user)):
    repo, storage, _, _ = _svc()
    try:
        lec = authz.get_lecture(repo, user["id"], lecture_id)
    except NotFoundError as e:
        raise _nf(e)
    _ready(lec)
    data = repo.viewer_data(lecture_id)
    for s in data["slides"]:
        s["image_url"] = storage.signed_url(s.pop("image_key"))
    media = [a for a in repo.list_assets(lecture_id, status="ready") if a["type"] in ("video", "audio")]
    data["media_url"] = storage.signed_url(media[-1]["storage_key"]) if media else None
    return data


@router.get("/files/{key:path}")
def files(key: str, exp: str = "", sig: str = ""):
    """Serves local-storage objects for valid, unexpired signed URLs (S3 deployments use pre-signed S3 URLs instead
    and never hit this route). Malformed exp/sig are simply 'invalid link' (403), never a 500; the content type is
    decided by the server from the key (never sniffed) and non-media objects are served as attachments."""
    storage = config.get_storage()
    if not isinstance(storage, LocalStorage) or not storage.verify_signed(key, exp, sig):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid or expired link")
    if not storage.exists(key):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    with storage.local_path(key) as path:
        return FileResponse(path, media_type=content_type_for(key),             # Starlette serves Range requests
                            headers={"Content-Disposition": disposition_for(key), "X-Content-Type-Options": "nosniff"})


# GET /lectures/{id}/search now lives in routes_workspace (citations, provenance, highlights).


@router.get("/evidence/{evidence_id}")
def evidence(evidence_id: int, user: dict = Depends(get_current_user)):
    try:
        r = EvidenceService(config.get_repo()).resolve(user["id"], evidence_id)
    except NotFoundError as e:
        raise _nf(e)
    if r["asset"]:
        r["asset"]["url"] = config.get_storage().signed_url(r["asset"].pop("storage_key"))
    return r


# ── corrections ──────────────────────────────────────────────────────────
class SegmentPatch(BaseModel):
    text: Optional[str] = Field(None, max_length=5000)
    slide_index: Optional[int] = Field(None, ge=1, le=100000)
    clear_slide: bool = False


class Boundary(BaseModel):
    slide_index: Optional[int] = Field(None, ge=1, le=100000)
    start: float = Field(..., ge=0, le=10 ** 6)
    end: float = Field(..., ge=0, le=10 ** 6)


@router.patch("/lectures/{lecture_id}/segments/{segment_id}")
def patch_segment(lecture_id: int, segment_id: int, body: SegmentPatch, user: dict = Depends(get_current_user)):
    repo = config.get_repo()
    try:
        _ready(authz.get_lecture(repo, user["id"], lecture_id))
        if body.text is not None and not repo.update_segment_text(lecture_id, segment_id, body.text):
            raise HTTPException(404, "not found")
        if body.slide_index is not None or body.clear_slide:
            if not repo.set_segment_slide(lecture_id, segment_id, None if body.clear_slide else body.slide_index):
                raise HTTPException(404, "not found")
    except NotFoundError as e:
        raise _nf(e)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.put("/lectures/{lecture_id}/timeline")
def put_timeline(lecture_id: int, body: List[Boundary], user: dict = Depends(get_current_user)):
    repo = config.get_repo()
    try:
        _ready(authz.get_lecture(repo, user["id"], lecture_id))
        n = corrections.set_slide_boundaries(repo, lecture_id, [b.model_dump() for b in body])
    except NotFoundError as e:
        raise _nf(e)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"realigned_segments": n}


# ── Ask (lecture-scoped; course scope is a later sprint) ─────────────────
class AskTurn(BaseModel):
    question: str = Field(..., max_length=2000)
    answer: str = Field(..., max_length=8000)


class AskIn(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    history: List[AskTurn] = Field(default_factory=list, max_length=50)


@router.post("/lectures/{lecture_id}/ask")
def ask_lecture(lecture_id: int, body: AskIn, user: dict = Depends(get_current_user)):
    """Stateless Q&A (Sprint 1 contract). Runs the same hardened retrieval/answer/validation pipeline as /chat;
    client-supplied `history` is treated as untrusted text. Prefer POST /lectures/{id}/chat (server-side history)."""
    try:
        return ask_handlers.ask_stateless(user["id"], lecture_id, body.question, [t.model_dump() for t in body.history])
    except ask_handlers.ApiError as e:
        raise HTTPException(e.status, e.detail)
