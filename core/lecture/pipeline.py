"""The lecture pipeline as ten independently retryable stages.

  VALIDATE -> PROBE -> EXTRACT_AUDIO -> TRANSCRIBE -> PROCESS_SLIDES ->
  EXTRACT_FRAMES -> DETECT_SLIDE_CHANGES -> ALIGN -> BUILD_EVIDENCE -> INDEX -> ANALYZE

ANALYZE (Sprint 2/3) derives key moments / summary sections and the retrieval
vectors from the stored evidence. It is best-effort: they are re-derivable (and regenerated lazily when
read), so a failure is recorded in the stage output but never fails the lecture.

Each stage reads its inputs from storage / earlier stage outputs (persisted
by jobs.JobService) and writes its output back, so a later failure never
re-runs an earlier stage. The stage bodies call the same FFmpeg / faster-
whisper / OpenCV matching / alignment code as before — only the plumbing
(assets + storage keys instead of paths, persisted stage outputs) is new.

Input modes (derived from the lecture's ready assets):
  video+slides · audio+slides (no frames -> segments stay unassigned) ·
  video only · audio only · slides only
"""
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from typing import Callable, Optional

from . import ai_study, align, config, insights, knowledge, media, retrieval, slide_match, slides as slides_mod, transcribe
from .errors import PermanentError
from .evidence import EvidenceService
from .jobs import JobService, Stage, StageContext
from .repo import LectureRepository
from .storage import StorageService

FRAME_INTERVAL = float(os.getenv("NOTIV_FRAME_INTERVAL", "2.0"))
MEDIA_TYPES, DECK_TYPES = ("video", "audio"), ("pdf", "pptx")


@dataclass
class Services:
    storage: StorageService
    transcriber: Callable        # (wav_path, language, on_progress, duration) -> [Segment]


def _derived(lecture_id: int, name: str) -> str:
    return f"lectures/{lecture_id}/derived/{name}"


def _plan(ctx: StageContext) -> dict:
    return ctx.prior("VALIDATE")


def _scratch() -> str:
    return tempfile.mkdtemp(prefix="notiv-stage-")


# ── stages ───────────────────────────────────────────────────────────────
def validate(ctx: StageContext) -> dict:
    ready = ctx.repo.list_assets(ctx.lecture["id"], status="ready")
    medias = [a for a in ready if a["type"] in MEDIA_TYPES]
    decks = [a for a in ready if a["type"] in DECK_TYPES]
    if not medias and not decks:
        raise PermanentError("lecture has neither a recording nor a slide deck")
    m, d = (medias[-1] if medias else None), (decks[-1] if decks else None)   # latest of each kind
    for a in (m, d):
        if a and not ctx.services.storage.exists(a["storage_key"]):
            raise PermanentError(f"asset {a['id']} is missing from storage")
    mode = ("video_slides" if m and m["type"] == "video" and d else "audio_slides" if m and d else
            f"{m['type']}_only" if m else "slides_only")
    return {"media_asset_id": m and m["id"], "media_key": m and m["storage_key"], "media_type": m and m["type"],
            "deck_asset_id": d and d["id"], "deck_key": d and d["storage_key"], "deck_type": d and d["type"],
            "mode": mode}


def probe(ctx: StageContext) -> dict:
    p = _plan(ctx)
    if not p["media_key"]:
        return ctx.skip("no recording")
    with ctx.services.storage.local_path(p["media_key"]) as path:
        info = media.probe(path)
    if not info["has_audio"]:
        raise media.MediaError("recording has no audio track to transcribe (a video without sound cannot be transcribed)")
    ctx.repo.update_asset(p["media_asset_id"], duration_seconds=info["duration"])
    ctx.repo.set_status(ctx.lecture["id"], "running", duration=info["duration"])
    return info


def extract_audio(ctx: StageContext) -> dict:
    p = _plan(ctx)
    if not p["media_key"]:
        return ctx.skip("no recording")
    info = ctx.prior("PROBE")
    probed = info.get("duration") or 0.0
    # truncation is judged against the audio track itself: a video whose picture runs on after the sound ends is not damaged
    expected = info.get("audio_duration") or probed
    tmp = _scratch()
    try:
        with ctx.services.storage.local_path(p["media_key"]) as src:
            wav = media.extract_audio(src, os.path.join(tmp, "audio.wav"), expected_duration=expected,
                                      format_name=info.get("format_name", ""))
        real = media.wav_info(wav)["duration"]               # what was ACTUALLY decoded (verified against the probe above)
        key = _derived(ctx.lecture["id"], "audio.wav")
        ctx.services.storage.put(key, wav)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    out = {"audio_key": key, "audio_seconds": round(real, 3), "probed_seconds": round(probed, 3)}
    if real > probed + media.duration_tolerance(probed, info.get("format_name", "")):
        # VBR MP3 without a length header: the container estimate was too short. The decoded length is the truth.
        ctx.repo.update_asset(p["media_asset_id"], duration_seconds=real)
        ctx.repo.set_status(ctx.lecture["id"], "running", duration=real)
        out["duration_corrected"] = True
    return out


def transcribe_stage(ctx: StageContext) -> dict:
    a = ctx.prior("EXTRACT_AUDIO")
    if not a.get("audio_key"):
        return ctx.skip("no recording")
    duration = max(ctx.prior("PROBE").get("duration") or 0.0, a.get("audio_seconds") or 0.0) or None
    with ctx.services.storage.local_path(a["audio_key"]) as wav:
        segs = ctx.services.transcriber(wav, ctx.lecture["language"],
                                        lambda f: ctx.progress(15 + int(f * 50)), duration)
    # Whatever produced the segments, they are checked here: ordered, inside the recording, no impossible overlaps.
    # A transcript that fails the check is NOT stored and the stage fails (and is retried), never marked ready.
    segs, report = transcribe.validate_segments(list(segs), duration)
    if not segs and not _plan(ctx).get("deck_key"):
        raise PermanentError("no speech was detected in the recording, so there is nothing to transcribe")
    if not segs:
        report["warnings"].append("no_speech_detected")
    return {"segments": [{"start": s.start, "end": s.end, "text": s.text, "source": s.source} for s in segs],
            "report": report}


def process_slides(ctx: StageContext) -> dict:
    p = _plan(ctx)
    if not p["deck_key"]:
        return ctx.skip("no slide deck")
    tmp = _scratch()
    try:
        with ctx.services.storage.local_path(p["deck_key"]) as src:
            ext = "." + p["deck_type"]
            local = os.path.join(tmp, "deck" + ext)
            shutil.copyfile(src, local)                       # soffice/pdftoppm want a normal name
            vision = config.get_vision_chain() if slides_mod.VISION_MODE != "off" else None
            deck = slides_mod.load_deck(local, os.path.join(tmp, "out"), vision=vision or None)
        if not deck:
            raise media.MediaError("the document produced no slides")
        out = []
        for s in deck:
            key = _derived(ctx.lecture["id"], f"slides/{s.index}.png")
            ctx.services.storage.put(key, s.image_path)       # same key every attempt: a retry overwrites, never duplicates
            out.append({"index": s.index, "key": key, "text": s.text, "width": s.width, "height": s.height,
                        "text_source": s.text_source, "ocr_confidence": s.ocr_confidence, "transcribed_by": s.transcribed_by,
                        "quality": s.quality})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # the stage only completes with every page stored
    for o in out:
        if not ctx.services.storage.exists(o["key"]):
            raise RuntimeError(f"slide {o['index']} image was not stored")
    no_text = [s.index for s in deck if "no_extractable_text" in s.warnings]
    why = {w: [s.index for s in deck if w in s.warnings] for w in ("ocr_unavailable", "ocr_failed", "ocr_low_confidence",
                                                                   "vision_failed", "vision_unreadable")}
    return {"slides": out, "count": len(out), "pages_without_text": no_text,
            "ocr_pages": [s.index for s in deck if s.text_source == "ocr"],
            "vision_pages": [s.index for s in deck if s.text_source == "vision"],
            "low_quality_pages": [s.index for s in deck if "low_quality_text" in s.warnings],
            "unreadable_pages": [s.index for s in deck if "unreadable_text_excluded" in s.warnings],
            "transcribed_by": sorted({s.transcribed_by for s in deck if s.transcribed_by}),
            "vision_errors": sorted({s.vision_error for s in deck if s.vision_error}),
            **{k: v for k, v in why.items() if v}}


def extract_frames(ctx: StageContext) -> dict:
    p, info = _plan(ctx), ctx.prior("PROBE")
    if not (p["media_key"] and p["deck_key"] and info.get("has_video")):
        return ctx.skip("frames are only needed for video + slides")
    tmp = _scratch()
    try:
        with ctx.services.storage.local_path(p["media_key"]) as src:
            frames = media.extract_frames(src, os.path.join(tmp, "f"), FRAME_INTERVAL)
        out = []
        for i, (t, path) in enumerate(frames):
            key = _derived(ctx.lecture["id"], f"frames/{i:06d}.jpg")
            ctx.services.storage.put(key, path)
            out.append({"t": t, "key": key})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"frames": out}


def detect_slide_changes(ctx: StageContext) -> dict:
    frames = ctx.prior("EXTRACT_FRAMES").get("frames")
    slides_out = ctx.prior("PROCESS_SLIDES").get("slides")
    if not frames or not slides_out:
        return ctx.skip("no frames or slides to match")
    st, p, duration = ctx.services.storage, _plan(ctx), ctx.prior("PROBE")["duration"]
    tmp = _scratch()
    try:
        slide_paths = [st.get(s["key"], os.path.join(tmp, f"s{s['index']}.png")) for s in slides_out]
        matcher = slide_match.SlideMatcher(slide_paths)
        results = []
        for i, f in enumerate(frames):
            results.append((f["t"], matcher.match(st.get(f["key"], os.path.join(tmp, f"f{i}.jpg")))))
        probe_path = os.path.join(tmp, "probe.jpg")
        with st.local_path(p["media_key"]) as video:
            timeline = slide_match.build_timeline(
                [(t, r[0]) for t, r in results], duration,
                classify_at=lambda t: matcher.match(media.extract_frame_at(video, t, probe_path))[0],
                scores=[r[1] for _, r in results])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"timeline": [asdict(e) for e in timeline]}


def align_stage(ctx: StageContext) -> dict:
    segs = [align.Segment(s["start"], s["end"], s["text"], s.get("source", "whisper"))
            for s in ctx.prior("TRANSCRIBE").get("segments", [])]
    tl = [slide_match.TimelineEntry(**e) for e in ctx.prior("DETECT_SLIDE_CHANGES").get("timeline", [])]
    return {"aligned": align.align_segments(segs, tl), "timeline": [asdict(e) for e in tl]}


def build_evidence(ctx: StageContext) -> dict:
    p, lid = _plan(ctx), ctx.lecture["id"]
    aligned = ctx.prior("ALIGN")
    slides_out = ctx.prior("PROCESS_SLIDES").get("slides", [])
    ids = ctx.repo.save_results(lid, slides_out, aligned["timeline"], aligned["aligned"])
    # completeness: what was produced upstream must be exactly what was stored; otherwise the lecture must NOT become ready
    if len(ids["slide_ids"]) != len(slides_out) or len(ids["segment_ids"]) != len(aligned["aligned"]):
        raise RuntimeError(f"stored {len(ids['slide_ids'])}/{len(slides_out)} slides and "
                           f"{len(ids['segment_ids'])}/{len(aligned['aligned'])} transcript lines")
    n = len(EvidenceService(ctx.repo).replace_for_lecture(
        lid, slides_out, ids["slide_ids"], aligned["aligned"], ids["segment_ids"],
        p["media_asset_id"], p["deck_asset_id"]))
    return {"slides": len(slides_out), "segments": len(ids["segment_ids"]), "evidence": n}


def index(ctx: StageContext) -> dict:
    return {"indexed": ctx.repo.rebuild_index(ctx.lecture["id"])}


def analyze(ctx: StageContext) -> dict:
    out = {}
    # AI study material (summary, concepts, flashcards, quiz, map) from the stored evidence; best-effort like the rest of
    # this stage: a provider failure is recorded with its real reason and the extractive outputs remain available
    out["ai_study"] = ai_study.ensure(ctx.repo, ctx.lecture["id"], force=True)
    try:
        out["generated"] = insights.ensure(ctx.repo, ctx.lecture["id"], force=True)
    except Exception as e:                                   # noqa: BLE001 — derived data, see module docstring
        out["skipped"] = f"analysis failed, will be generated on first read: {type(e).__name__}: {e}"
    try:                                                     # Sprint 3: retrieval vectors (also built lazily on first question)
        out["embedded"] = retrieval.ensure_embeddings(ctx.repo, ctx.lecture["id"], config.get_embedder())
    except Exception as e:                                   # noqa: BLE001
        out["embed_skipped"] = f"{type(e).__name__}"
    try:                                                     # Sprint 4: study knowledge (concepts, definitions, formulas, ...)
        out["knowledge"] = knowledge.regenerate(ctx.repo, ctx.lecture["id"])["total"]
    except Exception as e:                                   # noqa: BLE001 — built lazily on first study request
        out["knowledge_skipped"] = f"{type(e).__name__}"
    return out


STAGES = [
    Stage("VALIDATE", validate, 1), Stage("PROBE", probe, 2), Stage("EXTRACT_AUDIO", extract_audio, 5),
    Stage("TRANSCRIBE", transcribe_stage, 50), Stage("PROCESS_SLIDES", process_slides, 8),
    Stage("EXTRACT_FRAMES", extract_frames, 8), Stage("DETECT_SLIDE_CHANGES", detect_slide_changes, 10),
    Stage("ALIGN", align_stage, 1), Stage("BUILD_EVIDENCE", build_evidence, 3), Stage("INDEX", index, 2),
    Stage("ANALYZE", analyze, 2),
]
STAGE_NAMES = [s.name for s in STAGES]


def _cleanup(ctx: StageContext) -> None:
    """After a fully successful job: frames are re-derivable intermediates."""
    ctx.services.storage.delete_prefix(_derived(ctx.lecture["id"], "frames"))


def make_job_service(repo: LectureRepository, storage: StorageService, transcriber: Optional[Callable] = None,
                     backoff_base: float = 2.0, **kw) -> JobService:
    svc = Services(storage, transcriber or transcribe.transcribe)
    return JobService(repo, STAGES, lambda: svc, backoff_base=backoff_base, on_success=_cleanup, **kw)


def run_pipeline(repo: LectureRepository, storage: StorageService, lecture_id: int,
                 transcriber: Optional[Callable] = None) -> int:
    """Synchronous single-attempt run (CLI, scripts, tests). Returns the job
    id; re-raises the stage's original exception if the job failed."""
    js = make_job_service(repo, storage, transcriber)
    jid = js.enqueue(lecture_id, max_attempts=1)
    res = js.run_job(jid)
    if res.exception is not None:
        raise res.exception
    return jid
