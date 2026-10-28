"""Lecture workspace API (read side). Thin FastAPI wrappers over
workspace_handlers — all authorization/logic lives in core.lecture.workspace.

  GET /lectures/{id}/overview        state, progress, inputs, counts, warnings, media URL
  GET /lectures/{id}/timeline        slide spans + key-moment markers
  GET /lectures/{id}/transcript      timestamped segments (filter: start,end,slide; page: after_id,limit)
  GET /lectures/{id}/slides          slides with image URLs, text, appearances, seek times
  GET /lectures/{id}/evidence        evidence items with citation + provenance (filter: source_type,slide,start,end)
  GET /lectures/{id}/evidence/{eid}  one item + which insights it supports
  GET /lectures/{id}/search?q=       transcript+slide+evidence search with citations
  GET /lectures/{id}/key-moments     Notiv-generated key moments, each linked to evidence
  GET /lectures/{id}/summary         Notiv-generated summary sections, each linked to evidence
  GET /lectures/{id}/position        resolve one of t|slide|segment|evidence to the synchronized position

All require `Authorization: Bearer <token>` (server.api.deps.get_current_user).
Errors: 401 auth, 404 missing-or-not-yours, 409 not processed yet (detail has progress), 400 bad input.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

import server.api.workspace_handlers as h
from server.api.deps import get_current_user

router = APIRouter(tags=["lecture-workspace"])


def _run(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except h.ApiError as e:
        raise HTTPException(e.status, e.detail)


@router.get("/lectures/{lecture_id}/overview")
def overview(lecture_id: int, user: dict = Depends(get_current_user)):
    return _run(h.overview, user["id"], lecture_id)


@router.get("/lectures/{lecture_id}/timeline")
def timeline(lecture_id: int, user: dict = Depends(get_current_user)):
    return _run(h.timeline, user["id"], lecture_id)


@router.get("/lectures/{lecture_id}/transcript")
def transcript(lecture_id: int, start: Optional[float] = Query(None, ge=0), end: Optional[float] = Query(None, ge=0),
               slide: Optional[int] = Query(None, ge=1), after_id: int = Query(0, ge=0),
               limit: int = Query(500, ge=1, le=1000), user: dict = Depends(get_current_user)):
    return _run(h.transcript, user["id"], lecture_id, start, end, slide, after_id, limit)


@router.get("/lectures/{lecture_id}/slides")
def slides(lecture_id: int, user: dict = Depends(get_current_user)):
    return _run(h.slides, user["id"], lecture_id)


@router.get("/lectures/{lecture_id}/evidence")
def evidence(lecture_id: int, source_type: Optional[str] = Query(None, pattern="^(transcript|slide)$"),
             slide: Optional[int] = Query(None, ge=1), start: Optional[float] = Query(None, ge=0),
             end: Optional[float] = Query(None, ge=0), after_id: int = Query(0, ge=0),
             limit: int = Query(100, ge=1, le=200), user: dict = Depends(get_current_user)):
    return _run(h.evidence, user["id"], lecture_id, source_type, slide, start, end, after_id, limit)


@router.get("/lectures/{lecture_id}/evidence/{evidence_id}")
def evidence_item(lecture_id: int, evidence_id: int, user: dict = Depends(get_current_user)):
    return _run(h.evidence_item, user["id"], lecture_id, evidence_id)


@router.get("/lectures/{lecture_id}/search")
def search(lecture_id: int, q: str = Query(..., min_length=1, max_length=200), limit: int = Query(20, ge=1, le=100),
           source_type: Optional[str] = Query(None, pattern="^(transcript|slide)$"),
           user: dict = Depends(get_current_user)):
    return _run(h.search, user["id"], lecture_id, q, limit, source_type)


@router.get("/lectures/{lecture_id}/key-moments")
def key_moments(lecture_id: int, user: dict = Depends(get_current_user)):
    return _run(h.key_moments, user["id"], lecture_id)


@router.get("/lectures/{lecture_id}/summary")
def summary(lecture_id: int, user: dict = Depends(get_current_user)):
    return _run(h.summary, user["id"], lecture_id)


@router.get("/lectures/{lecture_id}/position")
def position(lecture_id: int, t: Optional[float] = Query(None, ge=0), slide: Optional[int] = Query(None, ge=1),
             segment: Optional[int] = Query(None, ge=1), evidence: Optional[int] = Query(None, ge=1),
             user: dict = Depends(get_current_user)):
    return _run(h.position, user["id"], lecture_id, t, slide, segment, evidence)
