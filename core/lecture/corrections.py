"""Student corrections. AI is sometimes wrong; these edits persist and are
never overwritten by a later boundary edit (manual segment->slide
assignments are skipped when the timeline is re-aligned).
Authorization is the caller's job (authz.get_lecture) before calling these."""
from typing import List

from . import align
from .repo import LectureRepository
from .slide_match import TimelineEntry


def set_slide_boundaries(repo: LectureRepository, lecture_id: int, boundaries: List[dict]) -> int:
    """boundaries: [{"slide_index": int|None, "start": s, "end": s}, ...] — the
    student's corrected timeline. Validated (ordered, non-overlapping, known
    slides), stored with confidence 1.0, then every NON-manually placed
    transcript segment is re-aligned. Returns segments re-aligned."""
    n_slides = repo.slide_count(lecture_id)
    prev_end, entries = 0.0, []
    for b in sorted(boundaries, key=lambda b: b["start"]):
        if b["end"] <= b["start"] or b["start"] < prev_end - 1e-6:
            raise ValueError("boundaries must be ordered, non-empty and non-overlapping")
        if b.get("slide_index") is not None and not 1 <= b["slide_index"] <= n_slides:
            raise ValueError(f"unknown slide {b['slide_index']}")
        entries.append(TimelineEntry(b.get("slide_index"), float(b["start"]), float(b["end"]), 1.0))
        prev_end = b["end"]
    repo.replace_timeline(lecture_id, [dict(slide_index=e.slide_index, start=e.start, end=e.end,
                                            confidence=e.confidence) for e in entries])
    segs = [s for s in repo.segments_for_realign(lecture_id) if not s["manual_slide"]]
    aligned = align.align_segments([align.Segment(s["start"], s["end"], s["text"]) for s in segs], entries)
    for s, g in zip(segs, aligned):
        repo.set_segment_slide(lecture_id, s["id"], g["slide_index"], manual=False, confidence=g["confidence"])
    return len(segs)
