"""Lecture position model: the single source of truth for synchronizing
media time, transcript segments and slides. Pure functions over data the
pipeline already produced (timeline entries + aligned segments) — no I/O.

  time  -> slide      slide_at(t)         (timeline span containing t)
  time  -> segment    segment_at(t)       (segment containing t; None in gaps)
  slide -> time       seek_for_slide(i)   (first span showing slide i; falls
                                           back to the first segment aligned to it)
  segment -> time     its own start

Nothing is invented: when the data can't answer (slides-only lecture, a slide
never matched in the video), the answer is None, never a guessed time.
"""
from bisect import bisect_right
from typing import List, Optional


class Sync:
    def __init__(self, timeline: List[dict], segments: List[dict]):
        self.timeline = sorted((dict(e) for e in timeline if e.get("start") is not None), key=lambda e: e["start"])
        self.segments = sorted((dict(s) for s in segments if s.get("start") is not None), key=lambda s: s["start"])
        self._tl_starts = [e["start"] for e in self.timeline]
        self._seg_starts = [s["start"] for s in self.segments]

    # ── time -> ... ──────────────────────────────────────────────────────
    def span_at(self, t: float) -> Optional[dict]:
        i = bisect_right(self._tl_starts, t) - 1
        if i < 0:
            return None
        e = self.timeline[i]
        return e if t < e["end"] or (i == len(self.timeline) - 1 and t <= e["end"]) else None

    def slide_at(self, t: float) -> Optional[int]:
        e = self.span_at(t)
        return e["slide_index"] if e else None

    def segment_at(self, t: float) -> Optional[dict]:
        i = bisect_right(self._seg_starts, t) - 1
        # segments may overlap slightly: scan back over the few that could still contain t
        for j in range(i, max(i - 4, -1), -1):
            s = self.segments[j]
            if s["start"] <= t < s["end"]:
                return s
        return None

    def position_at(self, t: float) -> dict:
        seg = self.segment_at(t)
        return {"seek_seconds": t, "slide_index": self.slide_at(t), "segment_id": seg["id"] if seg else None}

    # ── slide / segment -> time ──────────────────────────────────────────
    def spans_for_slide(self, slide_index: int) -> List[dict]:
        return [e for e in self.timeline if e["slide_index"] == slide_index]

    def seek_for_slide(self, slide_index: int) -> Optional[float]:
        spans = self.spans_for_slide(slide_index)
        if spans:
            return spans[0]["start"]
        for s in self.segments:
            if s.get("slide_index") == slide_index:
                return s["start"]
        return None

    def position_for_slide(self, slide_index: int) -> dict:
        t = self.seek_for_slide(slide_index)
        seg = self.segment_at(t) if t is not None else None
        return {"seek_seconds": t, "slide_index": slide_index, "segment_id": seg["id"] if seg else None}

    def position_for_segment(self, seg: dict) -> dict:
        t = seg["start"]
        slide = self.slide_at(t)
        # trust the stored alignment (it honours manual corrections) over a raw time lookup
        return {"seek_seconds": t, "slide_index": seg.get("slide_index") if seg.get("slide_index") is not None else slide,
                "segment_id": seg["id"]}
