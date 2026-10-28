"""Study intelligence API: knowledge, study items, study sessions, progress, course memory.
Thin FastAPI wrappers over study_handlers; all authorization lives in core.lecture.study / course_memory.

 Knowledge (lecture)
  GET    /lectures/{id}/knowledge?kind=&slide=&q=   concepts, definitions, formulas, facts, examples, emphasis, warnings
  GET    /lectures/{id}/concepts                    concept review view (each concept with its definitions/formulas/...)
  POST   /lectures/{id}/knowledge/regenerate        re-extract from current evidence (deletions persist, reviews survive)
  DELETE /knowledge/{kid}                           delete (tombstoned so regeneration won't recreate it)
  POST   /knowledge/{kid}/review                    mark a concept reviewed (per user)
 Study items
  GET    /lectures/{id}/study/items?kind=&difficulty=&reveal=   (answers hidden unless reveal=true)
  POST   /lectures/{id}/study/generate              {kinds?}  build/refresh generated items; says what it skipped and why
  GET|PATCH|DELETE /study/items/{iid}               full item w/ evidence | edit | delete
  POST   /study/items/{iid}/regenerate              rebuild one item from the lecture (MCQs get new distractors)
 Sessions / progress (private to the user)
  POST   /lectures/{id}/study/sessions   |  POST /courses/{cid}/study/sessions     {kinds?, difficulty?, limit?}
  GET    /study/sessions/{sid}              items WITHOUT answers until answered
  POST   /study/sessions/{sid}/attempts     {item_id, selected_option? | self_grade?, response_text?} -> graded + reveal
  POST   /study/sessions/{sid}/finish
  GET    /lectures/{id}/study/progress   |  GET /courses/{cid}/study/progress
 Course memory
  GET    /courses/{cid}/overview | /knowledge?kind=&lecture=&q= | /concepts?shared_only= | /search?q= | /study/items

Errors: 401 auth · 404 missing-or-not-yours · 409 not ready / conflict {code} · 400/422 input.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

import server.api.study_handlers as h
from server.api.deps import get_current_user

router = APIRouter(tags=["study"])


class GenerateIn(BaseModel):
    kinds: Optional[List[str]] = None


class SessionIn(BaseModel):
    kinds: Optional[List[str]] = None
    difficulty: Optional[str] = None
    limit: int = Field(10, ge=1, le=50)
    include_mastered: bool = False


class AttemptIn(BaseModel):
    item_id: int
    selected_option: Optional[str] = Field(None, max_length=8)
    self_grade: Optional[str] = Field(None, max_length=16)
    response_text: Optional[str] = Field(None, max_length=2000)


def _run(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except h.ApiError as e:
        raise HTTPException(e.status, e.detail)


# knowledge
@router.get("/lectures/{lecture_id}/knowledge")
def knowledge(lecture_id: int, kind: Optional[str] = Query(None), slide: Optional[int] = Query(None, ge=1),
              q: Optional[str] = Query(None, max_length=200), user: dict = Depends(get_current_user)):
    return _run(h.knowledge, user["id"], lecture_id, kind, slide, q)


@router.get("/lectures/{lecture_id}/concepts")
def concepts(lecture_id: int, user: dict = Depends(get_current_user)):
    return _run(h.concepts, user["id"], lecture_id)


@router.post("/lectures/{lecture_id}/knowledge/regenerate")
def knowledge_regenerate(lecture_id: int, user: dict = Depends(get_current_user)):
    return _run(h.knowledge_regenerate, user["id"], lecture_id)


@router.delete("/knowledge/{knowledge_id}")
def knowledge_delete(knowledge_id: int, user: dict = Depends(get_current_user)):
    return _run(h.knowledge_delete, user["id"], knowledge_id)


@router.post("/knowledge/{knowledge_id}/review")
def knowledge_review(knowledge_id: int, user: dict = Depends(get_current_user)):
    return _run(h.knowledge_review, user["id"], knowledge_id)


# study items
@router.get("/lectures/{lecture_id}/study/items")
def study_items(lecture_id: int, kind: Optional[str] = Query(None), difficulty: Optional[str] = Query(None),
                reveal: bool = Query(False), user: dict = Depends(get_current_user)):
    return _run(h.study_items, user["id"], lecture_id, kind, difficulty, reveal)


@router.post("/lectures/{lecture_id}/study/generate")
def study_generate(lecture_id: int, body: GenerateIn = Body(default_factory=GenerateIn), user: dict = Depends(get_current_user)):
    return _run(h.study_generate, user["id"], lecture_id, body.kinds)


@router.get("/study/items/{item_id}")
def item_get(item_id: int, user: dict = Depends(get_current_user)):
    return _run(h.item_get, user["id"], item_id)


@router.patch("/study/items/{item_id}")
def item_edit(item_id: int, body: Dict[str, Any] = Body(...), user: dict = Depends(get_current_user)):
    return _run(h.item_edit, user["id"], item_id, body)


@router.delete("/study/items/{item_id}")
def item_delete(item_id: int, user: dict = Depends(get_current_user)):
    return _run(h.item_delete, user["id"], item_id)


@router.post("/study/items/{item_id}/regenerate")
def item_regenerate(item_id: int, user: dict = Depends(get_current_user)):
    return _run(h.item_regenerate, user["id"], item_id)


# sessions + progress
@router.post("/lectures/{lecture_id}/study/sessions")
def session_start_lecture(lecture_id: int, body: SessionIn = Body(default_factory=SessionIn), user: dict = Depends(get_current_user)):
    return _run(h.session_start_lecture, user["id"], lecture_id, body.kinds, body.difficulty, body.limit, body.include_mastered)


@router.post("/courses/{course_id}/study/sessions")
def session_start_course(course_id: int, body: SessionIn = Body(default_factory=SessionIn), user: dict = Depends(get_current_user)):
    return _run(h.session_start_course, user["id"], course_id, body.kinds, body.difficulty, body.limit, body.include_mastered)


@router.get("/study/sessions/{session_id}")
def session_get(session_id: int, user: dict = Depends(get_current_user)):
    return _run(h.session_get, user["id"], session_id)


@router.post("/study/sessions/{session_id}/attempts")
def session_attempt(session_id: int, body: AttemptIn, user: dict = Depends(get_current_user)):
    return _run(h.session_attempt, user["id"], session_id, body.item_id, body.selected_option, body.self_grade, body.response_text)


@router.post("/study/sessions/{session_id}/finish")
def session_finish(session_id: int, user: dict = Depends(get_current_user)):
    return _run(h.session_finish, user["id"], session_id)


@router.get("/lectures/{lecture_id}/study/progress")
def progress_lecture(lecture_id: int, user: dict = Depends(get_current_user)):
    return _run(h.progress_lecture, user["id"], lecture_id)


@router.get("/courses/{course_id}/study/progress")
def progress_course(course_id: int, user: dict = Depends(get_current_user)):
    return _run(h.progress_course, user["id"], course_id)


# course memory
@router.get("/courses/{course_id}/overview")
def course_overview(course_id: int, user: dict = Depends(get_current_user)):
    return _run(h.course_overview, user["id"], course_id)


@router.get("/courses/{course_id}/knowledge")
def course_knowledge(course_id: int, kind: Optional[str] = Query(None), lecture: Optional[int] = Query(None, ge=1),
                     q: Optional[str] = Query(None, max_length=200), user: dict = Depends(get_current_user)):
    return _run(h.course_knowledge, user["id"], course_id, kind, lecture, q)


@router.get("/courses/{course_id}/concepts")
def course_concepts(course_id: int, shared_only: bool = Query(False), user: dict = Depends(get_current_user)):
    return _run(h.course_concepts, user["id"], course_id, shared_only)


@router.get("/courses/{course_id}/search")
def course_search(course_id: int, q: str = Query(..., min_length=1, max_length=200), limit: int = Query(20, ge=1, le=50),
                  user: dict = Depends(get_current_user)):
    return _run(h.course_search, user["id"], course_id, q, limit)


@router.get("/courses/{course_id}/study/items")
def course_study_items(course_id: int, kind: Optional[str] = Query(None), difficulty: Optional[str] = Query(None),
                       reveal: bool = Query(False), user: dict = Depends(get_current_user)):
    return _run(h.course_study_items, user["id"], course_id, kind, difficulty, reveal)
