from typing import List, Optional

from pydantic import BaseModel


class ChatTurn(BaseModel):
    """One prior question/answer pair, sent back by the client so a
    follow-up question can be understood in context. Optional/additive —
    a client that never sends this (old behavior) still works exactly
    as before, with an empty history."""
    question: str
    answer: str


class ChatRequest(BaseModel):
    meeting_id: int
    question: str
    history: List[ChatTurn] = []  # Phase 2C: optional, defaults to empty — backward compatible


class SourceChunk(BaseModel):
    """One retrieved transcript chunk the answer was (or would have
    been) grounded in. start/end/source are only ever populated when the
    underlying chunk actually has real timing (see vector_store.py) —
    None, never a guessed value, otherwise."""
    chunk_index: int
    text: str
    start: Optional[float] = None
    end: Optional[float] = None
    source: Optional[str] = None


class ChatResponse(BaseModel):
    meeting_id: int
    question: str
    answer: str
    sources: List[SourceChunk] = []  # Phase 2C: additive — old clients ignoring it still work


class SearchHit(BaseModel):
    chunk_index: int
    text: str
    start: Optional[float] = None  # Phase 2C: additive
    end: Optional[float] = None    # Phase 2C: additive
    source: Optional[str] = None   # Phase 2C: additive


class SearchResponse(BaseModel):
    meeting_id: int
    query: str
    results: List[SearchHit]
