"""Ask Notiv API (conversation + grounded retrieval). Thin FastAPI wrappers over ask_handlers.

  POST   /lectures/{id}/chat       {question}  -> grounded, cited answer; stored in YOUR conversation for this lecture
  GET    /lectures/{id}/chat                   -> your conversation (messages + citations in the evidence-item shape)
  DELETE /lectures/{id}/chat                   -> clear/reset your conversation
  POST   /lectures/{id}/chat/{message_id}/general-knowledge -> labelled general-knowledge explanation (needs an answer model)
  GET    /lectures/{id}/retrieve?q=&limit=     -> ranked evidence (hybrid), sufficiency verdict, scores — no generation

All require `Authorization: Bearer <token>`. Errors: 401 auth · 404 missing-or-not-yours · 409 lecture not ready ·
400/422 bad input · 429 rate limited (`Retry-After`).
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

import server.api.ask_handlers as h
from server.api.deps import get_current_user

router = APIRouter(tags=["ask-notiv"])


class ChatIn(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)


def _run(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except h.ApiError as e:
        headers = {"Retry-After": str(e.detail["retry_after"])} if e.status in (429, 503) and isinstance(e.detail, dict) and "retry_after" in e.detail else None
        raise HTTPException(e.status, e.detail, headers=headers)


@router.post("/lectures/{lecture_id}/chat")
def post_chat(lecture_id: int, body: ChatIn, user: dict = Depends(get_current_user)):
    return _run(h.chat, user["id"], lecture_id, body.question)


@router.post("/lectures/{lecture_id}/chat/{message_id}/general-knowledge")
def post_general_knowledge(lecture_id: int, message_id: int, user: dict = Depends(get_current_user)):
    """Separately labelled explanation from general knowledge (no citations) for an answer the lecture could not support."""
    return _run(h.general_knowledge, user["id"], lecture_id, message_id)


@router.get("/lectures/{lecture_id}/chat")
def get_chat(lecture_id: int, user: dict = Depends(get_current_user)):
    return _run(h.chat_history, user["id"], lecture_id)


@router.delete("/lectures/{lecture_id}/chat")
def delete_chat(lecture_id: int, user: dict = Depends(get_current_user)):
    return _run(h.chat_reset, user["id"], lecture_id)


@router.get("/lectures/{lecture_id}/retrieve")
def retrieve(lecture_id: int, q: str = Query(..., min_length=1, max_length=500), limit: int = Query(8, ge=1, le=20),
             user: dict = Depends(get_current_user)):
    return _run(h.retrieve, user["id"], lecture_id, q, limit)
