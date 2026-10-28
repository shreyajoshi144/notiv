"""FastAPI routes for personal notes. Thin wrappers over notes_handlers (the same functions the stdlib dev server and the
tests call). NOTE: written against FastAPI but NOT executed in the authoring sandbox (FastAPI isn't installable there);
core/test_sprint5_fastapi.py / test_notes.py's FastAPI class SKIP without it."""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, Field

from server.api import notes_handlers as h
from server.api.deps import get_current_user, http_error
from server.api.workspace_handlers import ApiError

router = APIRouter()


def _run(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ApiError as e:
        from fastapi import HTTPException
        raise HTTPException(e.status, e.detail)


class NoteIn(BaseModel):
    body: str = Field(..., max_length=20000)
    evidence_ids: Optional[List[int]] = Field(None, max_length=50)
    slide_index: Optional[int] = None
    t: Optional[float] = None

    model_config = {"extra": "forbid"}


@router.get("/lectures/{lecture_id}/notes")
def notes_list(lecture_id: int, after_id: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200),
               evidence_id: Optional[int] = Query(None, ge=1), user: dict = Depends(get_current_user)):
    return _run(h.notes_list, user["id"], lecture_id, after_id, limit, evidence_id)


@router.post("/lectures/{lecture_id}/notes")
def note_create(lecture_id: int, body: NoteIn, user: dict = Depends(get_current_user)):
    return _run(h.note_create, user["id"], lecture_id, body.body, body.evidence_ids, body.slide_index, body.t)


@router.get("/notes/{note_id}")
def note_get(note_id: int, user: dict = Depends(get_current_user)):
    return _run(h.note_get, user["id"], note_id)


@router.patch("/notes/{note_id}")
def note_update(note_id: int, body: Dict[str, Any] = Body(...), user: dict = Depends(get_current_user)):
    return _run(h.note_update, user["id"], note_id, body)


@router.delete("/notes/{note_id}")
def note_delete(note_id: int, user: dict = Depends(get_current_user)):
    return _run(h.note_delete, user["id"], note_id)
