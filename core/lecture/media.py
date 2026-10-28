"""FFmpeg helpers: probe, audio extraction, frame extraction.

All functions shell out to the real ffmpeg/ffprobe binaries and raise
MediaError with ffmpeg's stderr on failure — nothing is simulated.

Reliability rules (enforced here, not left to callers):
  * every subprocess has a bounded timeout and runs in its own process group, so a timeout kills the WHOLE tree
    (soffice, for example, leaves a long-lived soffice.bin child that a plain subprocess timeout never reaches);
  * extraction is VERIFIED: the produced WAV is opened and its real duration compared with the probed one, so a
    truncated / partially decoded file can never flow on to transcription as if it were complete;
  * attached cover art (an MP3's embedded JPEG) is not a video stream.
"""
import json
import math
import os
import shutil
import signal
import subprocess
import wave
from typing import List, Optional, Tuple

from .errors import PermanentError


class MediaError(PermanentError, RuntimeError):
    """Bad/unsupported media or missing ffmpeg: retrying will not help."""


class MediaTimeout(RuntimeError):
    """A subprocess exceeded its time budget. Transient (a loaded host may simply be slow), so the job may retry it."""


# Untrusted uploads must only ever be read as LOCAL FILES: a file that merely *looks* like media but is really an HLS /
# concat / sdp playlist could otherwise make ffmpeg open http:// or file:// URLs named inside it (SSRF / local file read).
PROTO = ["-protocol_whitelist", "file"]

WAV_RATE = 16000


def _kill_group(p: subprocess.Popen) -> None:
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            p.kill()
        except OSError:
            pass


def _run(cmd: list, timeout: int = 3600) -> subprocess.CompletedProcess:
    # stdin=DEVNULL: never inherit the caller's terminal — ffmpeg/ffprobe/soffice can block
    # (or be stopped by SIGTTIN) reading it when run from an interactive shell or test runner.
    # start_new_session: the child gets its own process group so a timeout can kill grandchildren too.
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             start_new_session=True)
    except FileNotFoundError:
        raise MediaError(f"{cmd[0]} not found on PATH — install it (FFmpeg for audio/video, poppler-utils for PDF, "
                         f"LibreOffice for PPTX)")
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(p)
        try:
            p.communicate(timeout=10)
        except Exception:                                   # noqa: BLE001 — already killed; just reap
            pass
        raise MediaTimeout(f"{cmd[0]} timed out after {timeout}s")
    except BaseException:
        _kill_group(p)
        raise
    if p.returncode != 0:
        raise MediaError(f"{cmd[0]} failed: {err.decode(errors='replace')[-600:]}")
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


def tools_status() -> dict:
    """Which external binaries are present. {"ffmpeg": bool, "ffprobe": bool, "pdftoppm": ..., "soffice": ...}"""
    return {n: shutil.which(n) is not None for n in ("ffmpeg", "ffprobe", "pdftoppm", "pdftotext", "pdfinfo")} | {
        "soffice": (shutil.which("soffice") or shutil.which("libreoffice")) is not None}


# Python packages the processing pipeline imports, with the formats that cannot be processed without each.
# opencv/numpy are imported at module level by slide_match (and so by pipeline): without them NOTHING is processed.
PYTHON_PACKAGES = {
    "faster_whisper": ("faster-whisper", ("mp3", "mp4")),
    "pptx": ("python-pptx", ("pptx",)),
    "cv2": ("opencv-python-headless", ("pdf", "pptx", "mp3", "mp4")),
    "numpy": ("numpy", ("pdf", "pptx", "mp3", "mp4")),
}
# External binaries -> formats that need them (see tools_status).
TOOL_FORMATS = {"ffmpeg": ("mp3", "mp4"), "ffprobe": ("mp3", "mp4"), "pdftoppm": ("pdf", "pptx"),
                "pdftotext": ("pdf", "pptx"), "pdfinfo": ("pdf", "pptx"), "soffice": ("pptx",)}


def python_packages_status() -> dict:
    """Which pipeline packages are importable, keyed by pip name. find_spec only: nothing heavy is imported."""
    import importlib.util
    out = {}
    for mod, (pip_name, _) in PYTHON_PACKAGES.items():
        try:
            out[pip_name] = importlib.util.find_spec(mod) is not None
        except (ImportError, ValueError):
            out[pip_name] = False
    return out


def processing_status() -> dict:
    """{"missing_tools": [...], "missing_packages": [...], "blocked_formats": [...], "ocr_available": bool} for this host.
    OCR (Tesseract) blocks no format: without it, pages with no text layer (scans) simply stay without text."""
    from .slides import ocr_available                         # lazy: slides imports this module
    tools, pkgs = tools_status(), python_packages_status()
    missing_tools = sorted(k for k, v in tools.items() if not v)
    missing_pkgs = sorted(k for k, v in pkgs.items() if not v)
    blocked = set()
    for t in missing_tools:
        blocked.update(TOOL_FORMATS.get(t, ()))
    for pip_name, fmts in PYTHON_PACKAGES.values():
        if pip_name in missing_pkgs:
            blocked.update(fmts)
    order = ("pdf", "pptx", "mp3", "mp4")
    return {"missing_tools": missing_tools, "missing_packages": missing_pkgs,
            "blocked_formats": [f for f in order if f in blocked], "ocr_available": ocr_available()}


def require_tools(*names: str) -> None:
    st = tools_status()
    missing = [n for n in names if not st.get(n)]
    if missing:
        raise MediaError("required tool(s) not installed: " + ", ".join(missing))


def _finite_pos(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 0 else None


def probe(path: str) -> dict:
    """{"duration", "has_audio", "has_video", "format_name", "audio": {...}|None, "video": {...}|None, "audio_streams": int}.

    `duration` is the container duration, or the longest stream duration when the container has none (some MP3/MP4
    files). It is an ESTIMATE for VBR MP3 without a Xing header — extract_audio() measures the real decoded length.
    Streams flagged attached_pic (cover art) are not video."""
    p = _run(["ffprobe", "-v", "error", *PROTO, "-print_format", "json",
              "-show_format", "-show_streams", path], timeout=60)
    try:
        info = json.loads(p.stdout)
    except ValueError:
        raise MediaError("ffprobe returned unreadable output")
    streams = info.get("streams", []) or []
    real = [s for s in streams if not (s.get("disposition") or {}).get("attached_pic")]
    audio = [s for s in real if s.get("codec_type") == "audio"]
    video = [s for s in real if s.get("codec_type") == "video"]
    fmt = info.get("format", {}) or {}
    duration = _finite_pos(fmt.get("duration"))
    if duration is None:
        duration = max([d for d in (_finite_pos(s.get("duration")) for s in real) if d] or [0.0])
    a0, v0 = (audio[0] if audio else None), (video[0] if video else None)
    return {"duration": duration, "has_audio": bool(audio), "has_video": bool(video),
            # the audio STREAM's own length: in a video it is often shorter than the container (picture continues after
            # the sound ends); None when the container does not say (then the container duration is the reference)
            "audio_duration": _finite_pos(a0.get("duration")) if a0 else None,
            "format_name": fmt.get("format_name", ""), "audio_streams": len(audio),
            "audio": a0 and {"codec": a0.get("codec_name"), "sample_rate": int(a0.get("sample_rate") or 0),
                             "channels": int(a0.get("channels") or 0)},
            "video": v0 and {"codec": v0.get("codec_name"), "width": int(v0.get("width") or 0), "height": int(v0.get("height") or 0)}}


def wav_info(path: str) -> dict:
    """Real properties of a PCM WAV, read from the file itself: {"duration", "sample_rate", "channels"}."""
    try:
        with wave.open(path, "rb") as w:
            rate, ch, frames = w.getframerate(), w.getnchannels(), w.getnframes()
    except (wave.Error, EOFError, OSError) as e:
        raise MediaError(f"extracted audio is not a readable WAV: {e}")
    if rate <= 0:
        raise MediaError("extracted audio has an invalid sample rate")
    return {"duration": frames / rate, "sample_rate": rate, "channels": ch}


def duration_tolerance(expected: float, format_name: str = "") -> float:
    """How much SHORTER than the probed duration an extraction may be before it counts as truncated.
    VBR MP3 durations are estimates, so MP3 gets a wider band than MP4."""
    pct = 0.05 if "mp3" in (format_name or "") else 0.01
    return max(2.0, expected * pct)


def extract_timeout(duration: Optional[float]) -> int:
    """Bounded but proportional: decoding is normally >50x realtime; allow 10% of the media length + 5 min."""
    return int(min(6 * 3600, max(300, 300 + (duration or 0) * 0.1)))


def extract_audio(video_path: str, out_wav: str, expected_duration: Optional[float] = None,
                  format_name: str = "") -> str:
    """16 kHz mono PCM WAV — the format Whisper models want. Maps the FIRST audio stream explicitly.
    If `expected_duration` is given, the produced file is checked against it and a truncated result raises MediaError
    (never a silently short WAV)."""
    os.makedirs(os.path.dirname(out_wav) or ".", exist_ok=True)
    if os.path.exists(out_wav):
        os.remove(out_wav)                                  # idempotent retry: never trust a leftover from a dead attempt
    try:
        _run(["ffmpeg", "-nostdin", "-y", "-v", "error", *PROTO, "-i", video_path, "-map", "0:a:0", "-vn", "-sn", "-dn",
              "-ac", "1", "-ar", str(WAV_RATE), "-c:a", "pcm_s16le", out_wav], timeout=extract_timeout(expected_duration))
    except BaseException:
        if os.path.exists(out_wav):
            os.remove(out_wav)                              # a half-written WAV must not survive a failed attempt
        raise
    if not os.path.exists(out_wav) or os.path.getsize(out_wav) <= 44:
        raise MediaError("audio extraction produced no audio")
    info = wav_info(out_wav)
    if expected_duration and expected_duration > 0:
        tol = duration_tolerance(expected_duration, format_name)
        if info["duration"] < expected_duration - tol:
            os.remove(out_wav)
            raise MediaError(f"audio extraction is truncated: decoded {info['duration']:.1f}s of {expected_duration:.1f}s "
                             f"(the file is damaged or incomplete)")
    return out_wav


def extract_frames(video_path: str, out_dir: str, interval: float = 2.0,
                   width: int = 640) -> List[Tuple[float, str]]:
    """Samples one frame every `interval` seconds. Returns [(t_seconds, path)].

    Frame k is taken at t = k * interval (ffmpeg's fps filter emits the first
    frame at t=0). Boundaries between slides are refined later with
    extract_frame_at(), so `interval` only bounds how many frames get matched.
    """
    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):                           # idempotent retry: drop frames from a dead attempt
        if f.startswith("f_") and f.endswith(".jpg"):
            os.remove(os.path.join(out_dir, f))
    pattern = os.path.join(out_dir, "f_%06d.jpg")
    _run(["ffmpeg", "-nostdin", "-y", "-v", "error", *PROTO, "-i", video_path, "-an",
          "-vf", f"fps=1/{interval},scale={width}:-2", "-q:v", "3", pattern], timeout=6 * 3600)
    files = sorted(f for f in os.listdir(out_dir) if f.startswith("f_") and f.endswith(".jpg"))
    return [(i * interval, os.path.join(out_dir, f)) for i, f in enumerate(files)]


def extract_frame_at(video_path: str, t: float, out_path: str, width: int = 640) -> str:
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    _run(["ffmpeg", "-nostdin", "-y", "-v", "error", *PROTO, "-ss", f"{max(0.0, t):.3f}", "-i", video_path,
          "-an", "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3", out_path], timeout=120)
    if not os.path.exists(out_path):
        raise MediaError(f"no frame at t={t:.2f}s (past end of video?)")
    return out_path
