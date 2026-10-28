"""
Focused tests for core/rag_engine.py's Phase 2C grounded chat.

No real Mistral call is made anywhere — chat_with_sources() loads a real
(but isolated, per-test) Chroma collection via a lightweight fake
embeddings class (see fake_embeddings_test_util.py) so retrieval itself
is genuinely exercised, then the LLM step is monkeypatched via
`rag_engine.get_llm` to a fake Runnable that just echoes back what it
was given — so these tests check OUR grounding/bounding/evidence-wiring
logic, not Mistral's actual answer quality.
"""

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core.rag_engine as rg
import core.vector_store as vs
from core.transcriber import TranscriptSegment
from fake_embeddings_test_util import FakeHashingEmbeddings

from langchain_core.runnables import RunnableLambda
from langchain_core.messages import AIMessage


@pytest.fixture(autouse=True)
def _fake_embeddings_and_isolated_chroma(monkeypatch, tmp_path):
    monkeypatch.setattr(vs, "get_embeddings", lambda: FakeHashingEmbeddings())
    monkeypatch.setattr(vs, "CHROMA_DIR", str(tmp_path / "vector_db"))
    yield


def _segments(*specs):
    return [TranscriptSegment(text=t, start=s, end=e, source=src) for t, s, e, src in specs]


def _echo_llm_capturing(capture):
    """Fake LLM: records the exact system/human text it was given and
    returns a canned string containing the context, so tests can assert
    on what actually reached the 'model'."""
    def _invoke(prompt_value):
        messages = prompt_value.to_messages()
        capture.append({"system": messages[0].content, "human": messages[1].content})
        return AIMessage(content=f"ANSWERED based on: {messages[0].content[-200:]}")
    return RunnableLambda(_invoke)


# ── Answer found ────────────────────────────────────────────────────

def test_answer_found_uses_retrieved_evidence(monkeypatch):
    segments = _segments(
        ("the launch date is confirmed for march third", 30.0, 34.0, "whisper"),
        ("marketing will announce it next week", 34.0, 37.0, "whisper"),
    )
    vs.build_vector_store("the launch date is confirmed for march third marketing will announce it next week",
                           collection_name="c_found", segments=segments)

    capture = []
    monkeypatch.setattr(rg, "get_llm", lambda: _echo_llm_capturing(capture))

    result = rg.chat_with_sources("c_found", "when is the launch date?")

    assert "sources" in result and len(result["sources"]) > 0
    assert any("launch date" in s["text"] for s in result["sources"])
    assert capture, "LLM should have been called since evidence was found"
    assert "launch date is confirmed" in capture[0]["system"]


# ── Answer absent (no relevant evidence at all) ────────────────────

def test_answer_absent_when_retrieval_finds_nothing(monkeypatch):
    # Build a collection, then simulate the retriever finding nothing
    # (e.g. an unrelated/empty meeting) via a fake retriever.
    vs.build_vector_store("some unrelated content about lunch orders",
                           collection_name="c_absent")

    llm_calls = []
    monkeypatch.setattr(rg, "get_llm", lambda: _echo_llm_capturing(llm_calls))

    class _EmptyRetriever:
        def invoke(self, question):
            return []

    monkeypatch.setattr(rg, "get_retriever", lambda vector_store, k=4: _EmptyRetriever())

    result = rg.chat_with_sources("c_absent", "what did the budget committee decide?")

    assert result["answer"] == rg.NOT_FOUND_MESSAGE
    assert result["sources"] == []
    assert llm_calls == []  # no LLM call at all — deterministic, no hallucination risk


# ── Timestamps / evidence surfaced correctly ──────────────────────

def test_timestamps_included_in_context_and_sources_when_available(monkeypatch):
    segments = _segments(
        ("we agreed to ship on friday", 125.0, 128.0, "whisper"),
    )
    vs.build_vector_store("we agreed to ship on friday", collection_name="c_ts", segments=segments)

    capture = []
    monkeypatch.setattr(rg, "get_llm", lambda: _echo_llm_capturing(capture))

    result = rg.chat_with_sources("c_ts", "when will we ship?")

    assert result["sources"][0]["start"] == 125.0
    assert result["sources"][0]["end"] == 128.0
    assert result["sources"][0]["source"] == "whisper"
    assert "[02:05]" in capture[0]["system"]  # 125s -> [02:05], genuinely shown to the model


def test_no_fabricated_timestamp_when_none_available(monkeypatch):
    vs.build_vector_store("plain transcript text with no timing at all", collection_name="c_no_ts")

    capture = []
    monkeypatch.setattr(rg, "get_llm", lambda: _echo_llm_capturing(capture))

    result = rg.chat_with_sources("c_no_ts", "what does the transcript say?")

    assert result["sources"][0]["start"] is None
    assert result["sources"][0]["end"] is None
    # No bracketed timestamp anywhere in what was shown to the model for this chunk.
    assert "[" not in capture[0]["human"] or True  # human is just the question; check system context instead
    context_part = capture[0]["system"]
    assert "plain transcript text" in context_part
    assert "[00:" not in context_part and "[0:" not in context_part


# ── Long transcript / bounded context ──────────────────────────────

def test_context_is_bounded_for_many_retrieved_chunks(monkeypatch):
    # Build a large collection and retrieve with a high k, then confirm
    # the assembled context never exceeds MAX_CONTEXT_CHARS by much (it
    # stops adding whole docs once the budget is hit).
    long_segments = _segments(*[
        (f"topic sentence number {i} about quarterly performance metrics review", float(i), float(i + 1), "whisper")
        for i in range(50)
    ])
    vs.build_vector_store(" ".join(s.text for s in long_segments), collection_name="c_long", segments=long_segments)

    capture = []
    monkeypatch.setattr(rg, "get_llm", lambda: _echo_llm_capturing(capture))

    result = rg.chat_with_sources("c_long", "quarterly performance metrics review", k=50)

    context_len = len(capture[0]["system"])
    # Bounded: shouldn't be anywhere near "all 50 chunks fully included".
    assert context_len < rg.MAX_CONTEXT_CHARS + 2000  # small slack for prompt scaffolding/one extra doc
    assert len(result["sources"]) < 50  # not every retrieved chunk made it into the bounded context


def test_context_always_includes_at_least_one_doc_even_if_it_alone_is_large(monkeypatch):
    huge_text = "word " * 3000  # far bigger than MAX_CONTEXT_CHARS alone
    segments = _segments((huge_text, 0.0, 10.0, "whisper"))
    vs.build_vector_store(huge_text, collection_name="c_huge", segments=segments)

    monkeypatch.setattr(rg, "get_llm", lambda: _echo_llm_capturing([]))
    result = rg.chat_with_sources("c_huge", "word")

    assert len(result["sources"]) == 1  # the one (oversized) doc is still included, not dropped to zero


# ── Follow-up question (bounded conversation history) ─────────────

def test_follow_up_question_includes_bounded_history(monkeypatch):
    vs.build_vector_store("the project deadline is next friday", collection_name="c_followup")

    capture = []
    monkeypatch.setattr(rg, "get_llm", lambda: _echo_llm_capturing(capture))

    history = [
        {"question": "what is the deadline?", "answer": "Next Friday."},
    ]
    rg.chat_with_sources("c_followup", "is that still on track?", history=history)

    assert "what is the deadline?" in capture[0]["system"]
    assert "Next Friday." in capture[0]["system"]


def test_history_is_bounded_to_last_n_turns(monkeypatch):
    vs.build_vector_store("some meeting content here", collection_name="c_history_bound")

    capture = []
    monkeypatch.setattr(rg, "get_llm", lambda: _echo_llm_capturing(capture))

    history = [{"question": f"question {i}", "answer": f"answer {i}"} for i in range(10)]
    rg.chat_with_sources("c_history_bound", "follow up", history=history)

    system_text = capture[0]["system"]
    # Only the most recent MAX_HISTORY_TURNS turns should appear.
    for i in range(10 - rg.MAX_HISTORY_TURNS):
        assert f"question {i}" not in system_text
    assert f"question {9}" in system_text


def test_no_history_produces_no_history_block(monkeypatch):
    vs.build_vector_store("some content", collection_name="c_no_history")
    capture = []
    monkeypatch.setattr(rg, "get_llm", lambda: _echo_llm_capturing(capture))

    rg.chat_with_sources("c_no_history", "a question", history=None)

    assert "Recent conversation" not in capture[0]["system"]


# ── Conflicting transcript evidence ────────────────────────────────

def test_conflicting_evidence_both_sides_reach_the_model(monkeypatch):
    # Two chunks that directly disagree about the same fact. Nothing in
    # our code should silently drop or favor one — both must reach the
    # model, and the prompt must instruct it to flag the conflict rather
    # than pick a side.
    segments = _segments(
        ("alice said the budget is fifty thousand dollars", 10.0, 12.0, "whisper"),
        ("bob said the budget is actually eighty thousand dollars", 200.0, 202.0, "whisper"),
    )
    vs.build_vector_store(
        "alice said the budget is fifty thousand dollars ... "
        "bob said the budget is actually eighty thousand dollars",
        collection_name="c_conflict", segments=segments,
    )

    capture = []
    monkeypatch.setattr(rg, "get_llm", lambda: _echo_llm_capturing(capture))

    result = rg.chat_with_sources("c_conflict", "what is the budget?", k=2)

    system_text = capture[0]["system"]
    assert "fifty thousand" in system_text
    assert "eighty thousand" in system_text
    assert "conflicting" in system_text.lower()  # the system prompt tells the model to flag conflicts
    # Both conflicting statements must be present in what was retrieved —
    # whether they land in one merged chunk or several, nothing is dropped.
    all_source_text = " ".join(s["text"] for s in result["sources"])
    assert "fifty thousand" in all_source_text
    assert "eighty thousand" in all_source_text
