"""
Focused tests for job_queue.py's Phase 2B wiring:
  - transcribe_all_with_timestamps() is used instead of transcribe_all()
  - `transcript` stays a plain str everywhere downstream (compat)
  - the 3 separate extraction calls are replaced by one extract_findings()
    call, fed the timestamped rendering when timestamps exist
  - db.complete_meeting() still receives the same field names/shapes

No real Mistral/Whisper/Sarvam/Chroma call is made — every heavy
dependency job_queue.py lazily imports inside _process() is monkeypatched
at its source module via the SAME `core.X` / `utils.X` qualified path
job_queue.py itself imports from (so the patched module object is the
one _process() actually picks up), not a separately-imported bare copy.
"""

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import core.job_queue as jq
from core.transcriber import Transcript, TranscriptSegment
import core.extractor as extractor_mod
import core.summarizer as summarizer_mod
import core.vector_store as vector_store_mod
import core.db as db
import utils.audio_processor as audio_processor_mod


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point core.db at a throwaway SQLite file for this test only, with
    one user row (meetings.user_id has a FOREIGN KEY to users.id)."""
    db_path = str(tmp_path / "test_notiv.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()
    db.create_user("testuser", "password123")
    return db


@pytest.fixture
def wired_worker(monkeypatch, tmp_path):
    """A JobWorker with every heavy/networked dependency stubbed out. We
    call _process() directly and synchronously (never touching the
    background thread/queue), so tests stay fast and deterministic."""
    worker = jq.JobWorker.__new__(jq.JobWorker)  # skip __init__'s thread start

    fake_chunk = str(tmp_path / "chunk_0.wav")
    open(fake_chunk, "wb").close()

    monkeypatch.setattr(audio_processor_mod, "process_input", lambda source: [fake_chunk])
    monkeypatch.setattr(summarizer_mod, "summarize", lambda transcript: f"SUMMARY({transcript})")
    monkeypatch.setattr(summarizer_mod, "generate_title", lambda transcript: "A Test Meeting")
    monkeypatch.setattr(vector_store_mod, "build_vector_store",
                         lambda transcript, collection_name=None, segments=None: None)

    return worker


def _make_transcript_with_segments():
    return Transcript(text="ship it friday", segments=[
        TranscriptSegment(text="ship it friday", start=90.0, end=92.0, source="whisper"),
    ])


_NO_FINDINGS = (
    "## Action Items\nNo action items found.\n\n"
    "## Decisions\nNo key decisions found.\n\n"
    "## Open Questions\nNo open questions found."
)


def test_process_uses_transcribe_all_with_timestamps(monkeypatch, wired_worker, isolated_db):
    calls = {}

    def fake_transcribe(chunks, language):
        calls["chunks"] = chunks
        calls["language"] = language
        return _make_transcript_with_segments()

    import core.transcriber as transcriber_mod
    monkeypatch.setattr(transcriber_mod, "transcribe_all_with_timestamps", fake_transcribe)
    # The old transcribe_all() name must NOT be what's called anymore.
    monkeypatch.setattr(transcriber_mod, "transcribe_all",
                         lambda *a, **k: (_ for _ in ()).throw(AssertionError("transcribe_all() should not be called")))
    monkeypatch.setattr(extractor_mod, "extract_findings", lambda t: _NO_FINDINGS)

    meeting_id = db.create_meeting(user_id=1, source="fake://video", language="english")
    wired_worker._process(meeting_id, "fake://video", "english")

    assert calls["language"] == "english"
    meeting = db.get_meeting(meeting_id)
    assert meeting["status"] == "done"
    assert meeting["transcript"] == "ship it friday"  # plain str, unchanged contract


def test_process_calls_extract_findings_once_not_three_extractors(monkeypatch, wired_worker, isolated_db):
    import core.transcriber as transcriber_mod
    monkeypatch.setattr(transcriber_mod, "transcribe_all_with_timestamps",
                         lambda chunks, language: _make_transcript_with_segments())

    findings_calls = []

    def fake_extract_findings(text):
        findings_calls.append(text)
        return ("## Action Items\n1. Ship it. Owner: Not specified. Deadline: Friday.\n\n"
                "## Decisions\n1. Ship on Friday.\n\n"
                "## Open Questions\nNo open questions found.")

    monkeypatch.setattr(extractor_mod, "extract_findings", fake_extract_findings)

    def _should_not_be_called(*a, **k):
        raise AssertionError("old per-field extractor should not be called anymore")
    monkeypatch.setattr(extractor_mod, "extract_action_items", _should_not_be_called)
    monkeypatch.setattr(extractor_mod, "extract_key_decisions", _should_not_be_called)
    monkeypatch.setattr(extractor_mod, "extract_questions", _should_not_be_called)

    meeting_id = db.create_meeting(user_id=1, source="fake://video", language="english")
    wired_worker._process(meeting_id, "fake://video", "english")

    assert len(findings_calls) == 1  # exactly one bounded call, not three

    meeting = db.get_meeting(meeting_id)
    assert "Ship it" in meeting["action_items_raw"]
    assert "Ship on Friday" in meeting["decisions_raw"]
    assert meeting["questions_raw"] == "No open questions found."


def test_process_feeds_timestamped_text_to_extract_findings(monkeypatch, wired_worker, isolated_db):
    import core.transcriber as transcriber_mod
    monkeypatch.setattr(transcriber_mod, "transcribe_all_with_timestamps",
                         lambda chunks, language: _make_transcript_with_segments())

    captured = {}

    def fake_extract_findings(text):
        captured["text"] = text
        return _NO_FINDINGS

    monkeypatch.setattr(extractor_mod, "extract_findings", fake_extract_findings)

    meeting_id = db.create_meeting(user_id=1, source="fake://video", language="english")
    wired_worker._process(meeting_id, "fake://video", "english")

    assert captured["text"] == "[01:30] ship it friday"  # real timestamp, propagated downstream


def test_process_falls_back_to_plain_text_when_no_timestamps(monkeypatch, wired_worker, isolated_db):
    import core.transcriber as transcriber_mod
    plain = Transcript(text="no timing here", segments=[])
    monkeypatch.setattr(transcriber_mod, "transcribe_all_with_timestamps",
                         lambda chunks, language: plain)

    captured = {}

    def fake_extract_findings(t):
        captured["text"] = t
        return _NO_FINDINGS

    monkeypatch.setattr(extractor_mod, "extract_findings", fake_extract_findings)

    meeting_id = db.create_meeting(user_id=1, source="fake://video", language="english")
    wired_worker._process(meeting_id, "fake://video", "english")

    assert captured["text"] == "no timing here"  # no invented timestamp brackets
    assert "[" not in captured["text"]


def test_process_preserves_db_field_contract(monkeypatch, wired_worker, isolated_db):
    """db.complete_meeting() must still be called with exactly the same
    field names as before (no schema change) — title, transcript, summary,
    action_items_raw, decisions_raw, questions_raw, word_count, chunk_count,
    collection_name, latency_sec, cost_usd."""
    import core.transcriber as transcriber_mod
    monkeypatch.setattr(transcriber_mod, "transcribe_all_with_timestamps",
                         lambda chunks, language: _make_transcript_with_segments())
    monkeypatch.setattr(extractor_mod, "extract_findings", lambda t: _NO_FINDINGS)

    captured = {}
    real_complete = db.complete_meeting

    def spy_complete_meeting(meeting_id, **fields):
        captured.update(fields)
        return real_complete(meeting_id, **fields)

    monkeypatch.setattr(db, "complete_meeting", spy_complete_meeting)

    meeting_id = db.create_meeting(user_id=1, source="fake://video", language="english")
    wired_worker._process(meeting_id, "fake://video", "english")

    expected_fields = {
        "title", "transcript", "summary", "action_items_raw", "decisions_raw",
        "questions_raw", "word_count", "chunk_count", "collection_name",
        "latency_sec", "cost_usd",
    }
    assert expected_fields.issubset(captured.keys())
    assert isinstance(captured["transcript"], str)


def test_stage_sequence_has_one_findings_stage_not_three():
    keys = [k for k, _, _ in jq.STAGE_SEQUENCE]
    assert "findings" in keys
    assert "action_items" not in keys
    assert "decisions" not in keys
    assert "questions" not in keys
