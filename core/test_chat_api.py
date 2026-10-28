"""
Focused tests for the Phase 2C API-layer wiring:
  - schemas/chat.py: new fields are additive/backward-compatible
  - services/rag_service.py: chat() returns {"answer","sources"} and
    passes history through; search() surfaces start/end/source
  - api/routes_chat.py: /chat endpoint unpacks the new dict shape into
    ChatResponse.sources, /search endpoint's SearchHit accepts the new
    optional timing fields

No real Mistral/Chroma call — core.rag_engine.chat_with_sources and
core.vector_store are monkeypatched at the service layer.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_ROOT = os.path.join(REPO_ROOT, "backend")
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, BACKEND_ROOT)

import pytest

from server.schemas.chat import ChatRequest, ChatResponse, ChatTurn, SourceChunk, SearchHit
import server.services.rag_service as rag_service


# ── schemas: additive/backward compatible ──────────────────────────

def test_chat_request_history_defaults_to_empty_list():
    req = ChatRequest(meeting_id=1, question="hello")
    assert req.history == []


def test_chat_request_accepts_history_turns():
    req = ChatRequest(meeting_id=1, question="follow up",
                       history=[{"question": "q1", "answer": "a1"}])
    assert req.history == [ChatTurn(question="q1", answer="a1")]


def test_chat_response_sources_default_empty_and_optional_fields_none():
    resp = ChatResponse(meeting_id=1, question="q", answer="a")
    assert resp.sources == []

    resp2 = ChatResponse(meeting_id=1, question="q", answer="a", sources=[
        {"chunk_index": 0, "text": "hi"},
    ])
    assert resp2.sources[0].start is None
    assert resp2.sources[0].end is None


def test_search_hit_accepts_timing_fields_and_defaults_to_none():
    hit = SearchHit(chunk_index=0, text="some text")
    assert hit.start is None and hit.end is None and hit.source is None

    hit2 = SearchHit(chunk_index=0, text="some text", start=1.0, end=2.0, source="whisper")
    assert hit2.start == 1.0


# ── rag_service.chat(): returns dict, passes history, meeting-scoped ──

def test_rag_service_chat_returns_answer_and_sources(monkeypatch):
    captured = {}

    def fake_chat_with_sources(collection_name, question, k=4, history=None):
        captured["collection_name"] = collection_name
        captured["question"] = question
        captured["history"] = history
        return {"answer": "the answer", "sources": [{"chunk_index": 0, "text": "evidence",
                                                        "start": 1.0, "end": 2.0, "source": "whisper"}]}

    monkeypatch.setattr(rag_service, "chat_with_sources", fake_chat_with_sources)

    meeting = {"status": "done", "collection_name": "meeting_42"}
    result = rag_service.chat(meeting, "what happened?", history=[{"question": "q", "answer": "a"}])

    assert captured["collection_name"] == "meeting_42"  # meeting-scoped
    assert result["answer"] == "the answer"
    assert result["sources"][0]["start"] == 1.0


def test_rag_service_chat_raises_when_meeting_not_ready():
    meeting = {"status": "processing", "collection_name": None}
    with pytest.raises(rag_service.MeetingNotReadyError):
        rag_service.chat(meeting, "anything")


def test_rag_service_search_surfaces_timing_metadata(monkeypatch):
    class _FakeDoc:
        def __init__(self, text, metadata):
            self.page_content = text
            self.metadata = metadata

    class _FakeRetriever:
        def invoke(self, query):
            return [_FakeDoc("evidence text", {"chunk_index": 3, "start": 5.0, "end": 8.0, "source": "whisper"})]

    monkeypatch.setattr(rag_service, "load_vector_store", lambda collection_name: object())
    monkeypatch.setattr(rag_service, "get_retriever", lambda vector_store, k=5: _FakeRetriever())

    meeting = {"status": "done", "collection_name": "meeting_7"}
    hits = rag_service.search(meeting, "query", k=5)

    assert hits[0]["chunk_index"] == 3
    assert hits[0]["start"] == 5.0
    assert hits[0]["end"] == 8.0
    assert hits[0]["source"] == "whisper"


# ── routes_chat.py: endpoint unpacks dict into ChatResponse ────────

def test_chat_endpoint_builds_response_with_sources(monkeypatch):
    import server.api.routes_chat as routes_chat

    monkeypatch.setattr(routes_chat, "_owned_meeting", lambda meeting_id, user: {
        "status": "done", "collection_name": "meeting_1",
    })
    monkeypatch.setattr(routes_chat.rag_service, "chat", lambda meeting, question, history=None: {
        "answer": "grounded answer",
        "sources": [{"chunk_index": 0, "text": "evidence", "start": 3.0, "end": 5.0, "source": "whisper"}],
    })

    body = ChatRequest(meeting_id=1, question="what happened?")
    resp = routes_chat.chat(body, user={"id": 1})

    assert isinstance(resp, ChatResponse)
    assert resp.answer == "grounded answer"
    assert resp.sources[0].start == 3.0
    assert resp.sources[0].source == "whisper"


def test_chat_endpoint_passes_history_through(monkeypatch):
    import server.api.routes_chat as routes_chat

    monkeypatch.setattr(routes_chat, "_owned_meeting", lambda meeting_id, user: {
        "status": "done", "collection_name": "meeting_1",
    })

    captured = {}

    def fake_chat(meeting, question, history=None):
        captured["history"] = history
        return {"answer": "ok", "sources": []}

    monkeypatch.setattr(routes_chat.rag_service, "chat", fake_chat)

    body = ChatRequest(meeting_id=1, question="follow up",
                        history=[{"question": "q1", "answer": "a1"}])
    routes_chat.chat(body, user={"id": 1})

    assert captured["history"] == [{"question": "q1", "answer": "a1"}]
