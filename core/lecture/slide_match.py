"""Match video frames to slide images and build a slide timeline.

Classical CV only (OpenCV/NumPy): each image is reduced to a normalized
grayscale + edge-structure vector, and a frame is assigned to the slide with
the highest normalized cross-correlation. No model, no randomness.
"""
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

import cv2
import numpy as np

W, H = 160, 90


def _autocrop_dark_borders(gray: np.ndarray) -> np.ndarray:
    """Strips letterbox/pillarbox bars (near-constant dark edges) from a frame."""
    def bar(line):  # near-constant AND dark
        return line.max() - line.min() < 10 and line.mean() < 40
    top, bottom, left, right = 0, gray.shape[0], 0, gray.shape[1]
    while top < bottom - 8 and bar(gray[top]):
        top += 1
    while bottom > top + 8 and bar(gray[bottom - 1]):
        bottom -= 1
    while left < right - 8 and bar(gray[:, left]):
        left += 1
    while right > left + 8 and bar(gray[:, right - 1]):
        right -= 1
    return gray[top:bottom, left:right]


def _unit(v: np.ndarray) -> np.ndarray:
    v = v.astype(np.float32).ravel()
    v -= v.mean()
    n = np.linalg.norm(v)
    return v / n if n > 1e-6 else v


def features(image_path: str, crop_bars: bool = True) -> np.ndarray:
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"cannot read image: {image_path}")
    if crop_bars:
        img = _autocrop_dark_borders(img)
    img = cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA)
    img = cv2.GaussianBlur(img, (3, 3), 0)
    gx = cv2.Sobel(img, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(img, cv2.CV_32F, 0, 1)
    edges = cv2.GaussianBlur(cv2.magnitude(gx, gy), (5, 5), 0)
    return np.concatenate([_unit(img), _unit(edges)]) / np.sqrt(2)  # unit norm overall


class SlideMatcher:
    def __init__(self, slide_image_paths: Sequence[str], min_score: float = 0.35):
        self.min_score = min_score
        # slides are clean renders: never crop them
        self.S = np.stack([features(p, crop_bars=False) for p in slide_image_paths])

    def match(self, frame_path: str) -> Tuple[Optional[int], float, float]:
        """Returns (slide_index_1based or None, best_score, margin_to_runner_up)."""
        scores = self.S @ features(frame_path)
        order = np.argsort(scores)[::-1]
        best = float(scores[order[0]])
        margin = best - float(scores[order[1]]) if len(order) > 1 else best
        idx = int(order[0]) + 1 if best >= self.min_score else None
        return idx, best, margin


@dataclass
class TimelineEntry:
    slide_index: Optional[int]   # None = no slide recognised (e.g. camera-only view)
    start: float
    end: float
    confidence: Optional[float] = None   # mean frame-match score over the run (0..1)


def _absorb_short_runs(labels: List[Optional[int]], min_run: int) -> List[Optional[int]]:
    """A run shorter than `min_run` samples is treated as noise (a transition
    frame, a pointer flash) and takes the label of the preceding run."""
    labels = list(labels)
    changed = True
    while changed:
        changed = False
        runs, i = [], 0
        while i < len(labels):
            j = i
            while j < len(labels) and labels[j] == labels[i]:
                j += 1
            runs.append((i, j))
            i = j
        for k, (a, b) in enumerate(runs):
            if b - a < min_run and len(runs) > 1:
                src = labels[runs[k - 1][0]] if k > 0 else labels[runs[k + 1][0]]
                for t in range(a, b):
                    labels[t] = src
                changed = True
                break
    return labels


def build_timeline(samples: List[Tuple[float, Optional[int]]], duration: float,
                   classify_at: Optional[Callable[[float], Optional[int]]] = None,
                   min_run: int = 2, tol: float = 0.5,
                   scores: Optional[Sequence[float]] = None) -> List[TimelineEntry]:
    """samples: [(t, slide_index|None)] at a fixed interval, in time order.

    Labels are de-noised, then each label change is refined by bisection
    (using `classify_at(t)` — which extracts and matches a real frame at t)
    so boundaries are accurate to `tol` seconds rather than the sample
    interval. Without `classify_at`, boundaries sit at the first sample of
    the new slide.
    """
    if not samples:
        return []
    times = [t for t, _ in samples]
    labels = _absorb_short_runs([l for _, l in samples], min_run)

    entries: List[TimelineEntry] = []
    run_start_i = 0
    for i in range(1, len(labels) + 1):
        if i == len(labels) or labels[i] != labels[run_start_i]:
            if i < len(labels) and classify_at is not None:
                lo, hi = times[i - 1], times[i]
                prev = labels[run_start_i]
                while hi - lo > tol:
                    mid = (lo + hi) / 2
                    if classify_at(mid) == prev:
                        lo = mid
                    else:
                        hi = mid
                boundary = hi
            else:
                boundary = times[i] if i < len(labels) else duration
            start = 0.0 if not entries else entries[-1].end
            conf = None
            if scores is not None:
                run = [scores[k] for k in range(run_start_i, i) if samples[k][1] == labels[run_start_i]]
                conf = round(float(sum(run) / len(run)), 4) if run else 0.0
            entries.append(TimelineEntry(labels[run_start_i], start, min(boundary, duration), conf))
            run_start_i = i
    if entries:
        entries[-1].end = duration
    return entries
