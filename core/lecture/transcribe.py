"""Timestamped speech-to-text with faster-whisper.

The model is imported lazily and loaded once per process. If faster-whisper
(or its model weights) are unavailable this raises TranscriptionError —
it never falls back to made-up text.
"""
import os
import threading
import time
from typing import Callable, List, Optional, Tuple

from .align import Segment
from .errors import PermanentError


class TranscriptionError(RuntimeError):
    """Model load / decode failure — may be transient (e.g. weight download)."""


class TranscriptionUnavailable(TranscriptionError, PermanentError):
    """faster-whisper is not installed: retrying cannot help."""


class TranscriptInvalid(TranscriptionError):
    """The recogniser returned timestamps that cannot be right (out of order, past the end of the media, ...). Treated as
    transient — a retry re-decodes — and, importantly, such a transcript is never stored or marked ready."""


MODEL_SIZE = os.getenv("NOTIV_WHISPER_MODEL", "small")
DEVICE = os.getenv("NOTIV_WHISPER_DEVICE", "auto")
COMPUTE = os.getenv("NOTIV_WHISPER_COMPUTE", "int8")

_model = None
_lock = threading.Lock()


def _get_model():
    global _model
    with _lock:
        if _model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as e:
                raise TranscriptionUnavailable("faster-whisper is not installed (pip install faster-whisper)") from e
            try:
                _model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE)
            except Exception as e:
                raise TranscriptionError(f"could not load Whisper model '{MODEL_SIZE}': {e}") from e
        return _model


def time_budget(duration: Optional[float]) -> float:
    """Wall-clock budget for one transcription. CPU int8 Whisper runs roughly 0.2-1x realtime; allow
    NOTIV_TRANSCRIBE_TIMEOUT_FACTOR x the media length (default 3) with a 10 minute floor, so a wedged decode fails the
    attempt (and is retried by the job layer) instead of holding a worker forever."""
    try:
        factor = float(os.getenv("NOTIV_TRANSCRIBE_TIMEOUT_FACTOR", "3"))
    except ValueError:
        factor = 3.0
    return max(600.0, (duration or 0.0) * max(0.5, factor))


def validate_segments(segs: List[Segment], duration: Optional[float]) -> Tuple[List[Segment], dict]:
    """Checks and normalises recogniser output; returns (clean_segments, report). Raises TranscriptInvalid when the
    timestamps are unusable. Rules:
      * empty text is dropped; NaN/inf/negative starts are invalid;
      * segments are ordered by start; `end` is never before `start`;
      * overlap with the previous segment is trimmed (Whisper overlaps by a fraction of a second); an overlap of 5 s or more is
        invalid;
      * a segment ending past the media duration is clamped if within max(2 s, 1%) and invalid otherwise.
    report = {"segments", "first_start", "last_end", "duration", "coverage_end_ratio", "clamped", "warnings"}."""
    import math
    clean: List[Segment] = []
    clamped = 0
    tol = max(2.0, (duration or 0.0) * 0.01)
    for s in sorted((x for x in segs if (x.text or "").strip()), key=lambda x: (x.start, x.end)):
        if not (math.isfinite(s.start) and math.isfinite(s.end)) or s.start < -0.5:
            raise TranscriptInvalid("transcript has an invalid timestamp")
        start, end = max(0.0, float(s.start)), float(s.end)
        if end < start:
            end = start
        if duration and start > duration + tol:
            raise TranscriptInvalid(f"transcript segment starts at {start:.1f}s, after the end of the {duration:.1f}s recording")
        if duration and end > duration:
            if end > duration + tol:
                raise TranscriptInvalid(f"transcript segment ends at {end:.1f}s, past the {duration:.1f}s recording")
            end, clamped = duration, clamped + 1
        if clean and start < clean[-1].end:
            if clean[-1].end - start >= 5.0:
                raise TranscriptInvalid("transcript segments overlap by several seconds")
            start = min(clean[-1].end, end)
            clamped += 1
        clean.append(Segment(start=start, end=end, text=s.text.strip(), source=getattr(s, "source", "whisper")))
    warnings = []
    last_end = clean[-1].end if clean else 0.0
    ratio = (last_end / duration) if duration else None
    if duration and clean and duration >= 120 and ratio < 0.5:
        warnings.append("transcript_ends_early")             # may be a long silent tail; reported, not fatal
    return clean, {"segments": len(clean), "first_start": clean[0].start if clean else None, "last_end": last_end,
                   "duration": duration, "coverage_end_ratio": ratio, "clamped": clamped, "warnings": warnings}


def transcribe(wav_path: str, language: Optional[str] = None,
               on_progress: Optional[Callable[[float], None]] = None,
               duration: Optional[float] = None) -> List[Segment]:
    """Segments with real start/end seconds from the audio. `language=None`
    lets Whisper auto-detect. VAD filtering skips silence so slide-only
    stretches don't produce hallucinated text. The result is validated (order, bounds) before it is returned and the
    decode runs against a wall-clock budget (see time_budget)."""
    model = _get_model()
    deadline = time.monotonic() + time_budget(duration)
    seg_iter, _info = model.transcribe(wav_path, language=language, vad_filter=True,
                                       word_timestamps=False)
    out: List[Segment] = []
    for s in seg_iter:  # lazy generator: decoding happens as we iterate
        if time.monotonic() > deadline:
            raise TranscriptionError("transcription exceeded its time budget")
        text = s.text.strip()
        if text:
            out.append(Segment(start=float(s.start), end=float(s.end), text=text))
        if on_progress and duration:
            on_progress(min(1.0, float(s.end) / duration))
    return validate_segments(out, duration)[0]
