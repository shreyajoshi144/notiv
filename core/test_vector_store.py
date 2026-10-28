"""
Focused tests for core/vector_store.py's Phase 2C changes:
  - build_vector_store(transcript, segments=...) propagates real
    start/end/source metadata into Chroma documents
  - callers that don't pass segments see byte-for-byte the same
    behavior as before (backward compatibility)
  - no timestamp is fabricated when a segment's timing is unknown

Uses a REAL Chroma collection (already installed, no torch needed) with
a lightweight, deterministic fake embeddings class instead of the real
HuggingFaceEmbeddings (which needs sentence-transformers/torch, not
installed in this sandbox) — see fake_embeddings_test_util.py. No real
network/model call is made anywhere.
"""

import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import vector_store as vs
from transcriber import TranscriptSegment
from fake_embeddings_test_util import FakeHashingEmbeddings


@pytest.fixture(autouse=True)
def _fake_embeddings_and_isolated_chroma(monkeypatch, tmp_path):
    monkeypatch.setattr(vs, "get_embeddings", lambda: FakeHashingEmbeddings())
    monkeypatch.setattr(vs, "CHROMA_DIR", str(tmp_path / "vector_db"))
    yield


def _segments(*specs):
    """specs: (text, start, end, source) tuples."""
    return [TranscriptSegment(text=t, start=s, end=e, source=src) for t, s, e, src in specs]


# ── Backward compatibility: no segments -> unchanged behavior ─────────

def test_no_segments_produces_same_shape_as_before():
    transcript = "the quarterly numbers looked strong this year. " * 20
    store = vs.build_vector_store(transcript, collection_name="c_plain")
    docs = store.get()  # raw Chroma get() returns stored docs+metadatas
    assert len(docs["metadatas"]) > 0
    for meta in docs["metadatas"]:
        assert set(meta.keys()) == {"chunk_index"}  # no start/end/source keys at all
        assert "start" not in meta and "end" not in meta


def test_empty_segments_list_falls_back_to_plain_split():
    transcript = "hello world, this is a short transcript."
    store = vs.build_vector_store(transcript, collection_name="c_empty_segs", segments=[])
    docs = store.get()
    for meta in docs["metadatas"]:
        assert "start" not in meta


# ── Segments provided: real timestamp/source metadata propagated ─────

def test_segments_produce_start_end_source_metadata():
    segments = _segments(
        ("we will ship the report", 0.0, 3.0, "whisper"),
        ("by friday afternoon", 3.0, 5.0, "whisper"),
    )
    store = vs.build_vector_store("we will ship the report by friday afternoon",
                                   collection_name="c_segs", segments=segments)
    docs = store.get()
    metas = docs["metadatas"]
    assert len(metas) >= 1
    combined = " ".join(docs["documents"])
    assert "ship the report" in combined
    assert "friday afternoon" in combined
    for meta in metas:
        assert meta["start"] == pytest.approx(0.0)
        assert meta["end"] == pytest.approx(5.0)
        assert meta["source"] == "whisper"


def test_segments_start_new_chunk_once_size_budget_exceeded():
    long_text_a = "alpha " * 90   # > SEGMENT_CHUNK_CHARS on its own
    long_text_b = "beta " * 90
    segments = _segments(
        (long_text_a, 0.0, 60.0, "whisper"),
        (long_text_b, 60.0, 120.0, "whisper"),
    )
    docs = vs._group_segments_into_docs(segments, chunk_chars=vs.SEGMENT_CHUNK_CHARS)
    assert len(docs) == 2
    assert docs[0].metadata["start"] == 0.0
    assert docs[0].metadata["end"] == 60.0
    assert docs[1].metadata["start"] == 60.0
    assert docs[1].metadata["end"] == 120.0


def test_mixed_sources_labeled_mixed():
    segments = _segments(
        ("whisper part", 0.0, 1.0, "whisper"),
        ("sarvam part", 1.0, 2.0, "sarvam_offset"),
    )
    docs = vs._group_segments_into_docs(segments, chunk_chars=10_000)  # force them into one chunk
    assert len(docs) == 1
    assert docs[0].metadata["source"] == "mixed"


# ── Never invent a timestamp for a segment with unknown timing ───────

def test_segment_with_none_timing_gets_no_fabricated_timestamp():
    segments = _segments(
        ("no timing available here", None, None, "sarvam_offset"),
    )
    docs = vs._group_segments_into_docs(segments)
    assert len(docs) == 1
    assert "start" not in docs[0].metadata
    assert "end" not in docs[0].metadata
    # source can still be recorded even without timing — it's not a timestamp
    assert docs[0].metadata.get("source") == "sarvam_offset"


def test_partial_timing_within_a_chunk_only_uses_whats_known():
    # First segment has real timing, second in the same chunk doesn't —
    # the chunk's start comes from the first known start, end stays at
    # the last known end (never advances past what's actually known).
    segments = _segments(
        ("known timing", 10.0, 12.0, "whisper"),
        ("unknown timing", None, None, "whisper"),
    )
    docs = vs._group_segments_into_docs(segments, chunk_chars=10_000)
    assert len(docs) == 1
    assert docs[0].metadata["start"] == 10.0
    assert docs[0].metadata["end"] == 12.0  # not extended/guessed for the second segment


def test_empty_segment_text_is_skipped_not_included():
    segments = _segments(
        ("", 0.0, 1.0, "whisper"),
        ("real text", 1.0, 2.0, "whisper"),
    )
    docs = vs._group_segments_into_docs(segments)
    assert len(docs) == 1
    assert docs[0].page_content == "real text"
    assert docs[0].metadata["start"] == 1.0
