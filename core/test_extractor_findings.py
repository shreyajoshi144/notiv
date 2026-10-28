"""
Focused tests for extractor.py's Phase 2B additions:
  - extract_findings()            one bounded call instead of three
  - get_findings_section()        pure parsing back into 3 fields
  - build_timestamped_transcript()  real timestamps, never invented

No real Mistral API call is made anywhere — `build_chain` is
monkeypatched with a fake chain, same pattern as test_extractor.py.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import extractor as ex
from transcriber import Transcript, TranscriptSegment


class _FakeChain:
    def __init__(self, response=None):
        self.calls = []
        self._response = response

    def invoke(self, text):
        self.calls.append(text)
        if self._response is not None:
            return self._response
        return (
            "## Action Items\n1. Ship the report. Owner: Not specified. Deadline: Not specified.\n\n"
            "## Decisions\n1. Ship on Friday.\n\n"
            "## Open Questions\n1. Who owns QA?"
        )


@pytest.fixture
def fake_chain(monkeypatch):
    chain = _FakeChain()
    monkeypatch.setattr(ex, "build_chain", lambda system_prompt: chain)
    return chain


# ── extract_findings(): one bounded call replaces three ───────────────

def test_extract_findings_makes_exactly_one_call_for_short_transcript(fake_chain):
    transcript = "short meeting transcript, well under the limit"
    result = ex.extract_findings(transcript)

    assert len(fake_chain.calls) == 1
    assert fake_chain.calls[0] == transcript
    assert "## Action Items" in result
    assert "## Decisions" in result
    assert "## Open Questions" in result


def test_extract_findings_uses_the_findings_prompt(monkeypatch):
    captured = {}

    def fake_build_chain(system_prompt):
        captured["prompt"] = system_prompt
        return _FakeChain()

    monkeypatch.setattr(ex, "build_chain", fake_build_chain)
    ex.extract_findings("some transcript")

    assert captured["prompt"] == ex.FINDINGS_PROMPT
    assert "Owner: Not specified" in captured["prompt"]
    assert "NEVER invent" in captured["prompt"]


def test_extract_findings_bounds_long_transcripts(fake_chain):
    transcript = "word " * (ex.MAX_TRANSCRIPT_CHARS_PER_CALL // 2)
    assert len(transcript) > ex.MAX_TRANSCRIPT_CHARS_PER_CALL

    result = ex.extract_findings(transcript)

    assert len(fake_chain.calls) > 1  # same bounded chunking as the other extractors
    for call_text in fake_chain.calls:
        assert len(call_text) <= ex.MAX_TRANSCRIPT_CHARS_PER_CALL
    assert "Part 1/" in result


# ── get_findings_section(): pure parsing, no LLM ───────────────────────

STRUCTURED = (
    "## Action Items\n"
    "1. Alice to finalize budget. Owner: Alice. Deadline: Friday.\n\n"
    "## Decisions\n"
    "1. Ship feature X in Q3.\n\n"
    "## Open Questions\n"
    "1. Who owns the migration?"
)


@pytest.mark.parametrize("header,expected_snippet", [
    ("Action Items", "Alice to finalize"),
    ("Decisions", "Ship feature X"),
    ("Open Questions", "migration"),
])
def test_get_findings_section_extracts_each_section(header, expected_snippet):
    assert expected_snippet in ex.get_findings_section(STRUCTURED, header)


def test_get_findings_section_missing_header_returns_default():
    text = "## Action Items\nNo action items found."
    assert ex.get_findings_section(text, "Decisions") == ex._FINDINGS_DEFAULTS["Decisions"]


def test_get_findings_section_handles_multi_part_output():
    multi_part = (
        "— Part 1/2 —\n"
        "## Action Items\n1. Item from part 1.\n\n"
        "## Decisions\nNo key decisions found.\n\n"
        "## Open Questions\nNo open questions found.\n\n"
        "— Part 2/2 —\n"
        "## Action Items\n1. Item from part 2.\n\n"
        "## Decisions\n1. Decision only in part 2.\n\n"
        "## Open Questions\nNo open questions found."
    )
    action_items = ex.get_findings_section(multi_part, "Action Items")
    assert "Item from part 1" in action_items
    assert "Item from part 2" in action_items

    decisions = ex.get_findings_section(multi_part, "Decisions")
    assert "Decision only in part 2" in decisions


def test_get_findings_section_case_insensitive():
    text = "## action items\nlowercase header body"
    assert ex.get_findings_section(text, "Action Items") == "lowercase header body"


# ── build_timestamped_transcript(): real timing, never invented ──────

def test_build_timestamped_transcript_uses_real_segments():
    t = Transcript(text="hello world", segments=[
        TranscriptSegment(text="hello", start=0.0, end=1.0, source="whisper"),
        TranscriptSegment(text="world", start=65.0, end=66.0, source="whisper"),
    ])
    out = ex.build_timestamped_transcript(t)
    assert out == "[00:00] hello\n[01:05] world"


def test_build_timestamped_transcript_falls_back_without_segments():
    t = Transcript(text="plain text, no segments", segments=[])
    assert ex.build_timestamped_transcript(t) == "plain text, no segments"


def test_build_timestamped_transcript_falls_back_when_no_start_times_known():
    # Sarvam-with-no-timing edge case: segments exist but start is None.
    t = Transcript(text="no timing available", segments=[
        TranscriptSegment(text="no timing available", start=None, end=None, source="sarvam_offset"),
    ])
    assert ex.build_timestamped_transcript(t) == "no timing available"


def test_build_timestamped_transcript_accepts_plain_str():
    # A bare str (e.g. from the old transcribe_all()) must not crash —
    # just falls back to itself, no timestamps.
    assert ex.build_timestamped_transcript("just a plain string") == "just a plain string"


def test_extract_findings_end_to_end_with_timestamped_input(fake_chain):
    t = Transcript(text="ship it friday", segments=[
        TranscriptSegment(text="ship it friday", start=90.0, end=92.0, source="whisper"),
    ])
    timestamped = ex.build_timestamped_transcript(t)
    ex.extract_findings(timestamped)

    assert fake_chain.calls == ["[01:30] ship it friday"]
