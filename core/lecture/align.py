"""Temporal alignment of transcript segments to the slide timeline."""
from dataclasses import dataclass
from typing import List, Optional

from .slide_match import TimelineEntry


@dataclass
class Segment:
    start: float
    end: float
    text: str
    source: str = "whisper"


def overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def align_segments(segments: List[Segment], timeline: List[TimelineEntry]) -> List[dict]:
    """Assigns each segment to the timeline entry it overlaps most.

    Returns one dict per segment: {start, end, text, slide_index,
    straddles}. `slide_index` is None when the best-overlapping entry has no
    recognised slide, or when the segment overlaps nothing. `straddles` is
    True when the segment crosses a slide change (so a viewer can show it
    under both neighbours if it wants).
    """
    out = []
    for seg in segments:
        best, best_ov, touched = None, 0.0, 0
        for e in timeline:
            ov = overlap(seg.start, seg.end, e.start, e.end)
            if ov > 0:
                touched += 1
            if ov > best_ov:
                best, best_ov = e, ov
        dur = max(seg.end - seg.start, 1e-6)
        conf = None
        if best is not None and best.slide_index is not None:
            conf = round((best_ov / dur) * (best.confidence if best.confidence is not None else 1.0), 4)
        out.append({
            "start": seg.start, "end": seg.end, "text": seg.text,
            "slide_index": best.slide_index if best else None,
            "straddles": touched > 1,
            "confidence": conf,   # None when no slide is assigned
        })
    return out
