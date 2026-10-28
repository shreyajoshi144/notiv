"""Lecture workspace service: everything the workspace UI/API reads.

Reuses Sprint 1 pieces — authz (workspace membership), repo (store), storage
(signed URLs), jobs/stages (progress), evidence — and adds only presentation:
timestamps + source citations on every item, provenance labels, position
sync, search with citations, and generated insights.

Every public method takes `user_id` and authorizes through authz first.
Missing and not-yours are both NotFoundError. Content endpoints raise
NotReadyError (with state/stage/progress/error) until processing is done.
"""
import re
from typing import List, Optional

from . import ai_study, authz, insights
from .errors import NotFoundError, NotReadyError, ValidationError
from .provenance import is_lecture_evidence, mmss, origin_of
from .repo import LectureRepository
from .storage import StorageService
from .sync import Sync

MAX_LIMIT = 200
MAX_QUERY = 200
MEDIA_URL_TTL = 3600


def _state(lec: dict) -> str:
    return {"done": "ready", "failed": "failed", "running": "processing", "queued": "queued"}.get(lec["status"], lec["status"])


class WorkspaceService:
    def __init__(self, repo: LectureRepository, storage: StorageService):
        self.repo, self.storage = repo, storage

    # ── access ───────────────────────────────────────────────────────────
    def _lecture(self, user_id: int, lecture_id: int) -> dict:
        return authz.get_lecture(self.repo, user_id, lecture_id)

    def _ready(self, user_id: int, lecture_id: int) -> dict:
        lec = self._lecture(user_id, lecture_id)
        if lec["status"] != "done":
            raise NotReadyError(f"lecture is {lec['status']}", self._progress(lec))
        return lec

    def _progress(self, lec: dict) -> dict:
        jobs = self.repo.list_jobs(lec["id"])
        job = jobs[-1] if jobs else None
        return {"state": _state(lec), "status": lec["status"], "stage": lec["stage"],
                "progress_pct": lec["progress_pct"] or 0, "error": lec["error"],
                "job": None if not job else {k: job[k] for k in ("id", "status", "attempt", "max_attempts", "progress", "error")},
                "stages": [{k: s[k] for k in ("stage", "status", "attempt", "error")}
                           for s in (self.repo.list_stages(job["id"]) if job else [])],
                "can_retry": bool(job and job["status"] in ("failed", "cancelled"))}

    # ── shared context ───────────────────────────────────────────────────
    def _ctx(self, lecture_id: int) -> dict:
        d = self.repo.viewer_data(lecture_id)
        sync = Sync(d["timeline"], d["segments"])
        media = [a for a in self.repo.list_assets(lecture_id, status="ready") if a["type"] in ("video", "audio")]
        decks = [a for a in self.repo.list_assets(lecture_id, status="ready") if a["type"] in ("pdf", "pptx")]
        return {"data": d, "sync": sync, "media": media[-1] if media else None, "deck": decks[-1] if decks else None,
                "slide_text": {s["idx"]: s["text"] for s in d["slides"]}}

    @staticmethod
    def _source(asset: Optional[dict]) -> Optional[dict]:
        return None if not asset else {"asset_id": asset["id"], "type": asset["type"], "filename": asset["original_filename"]}

    def _citation(self, ev: dict, cx: dict) -> dict:
        """Where this evidence is, in the lecture's own terms. `seek_seconds` is
        the recording time to jump to; null when the data can't say (e.g. a
        slides-only lecture) — never guessed."""
        slide, seek = ev.get("slide_index"), ev.get("start")
        if seek is None and slide is not None and ev["source_type"] == "slide":
            seek = cx["sync"].seek_for_slide(slide)
        bits = ([f"Slide {slide}"] if slide is not None else []) + ([mmss(seek)] if seek is not None else [])
        asset = cx["media"] if ev["source_type"] == "transcript" else cx["deck"]
        src = self.repo.get_asset(ev["source_asset_id"]) if ev.get("source_asset_id") else asset
        return {"label": " · ".join(bits) or ev["source_type"], "seek_seconds": seek, "slide_index": slide,
                "start": ev.get("start"), "end": ev.get("end"), "source": self._source(src)}

    def _present(self, ev: dict, cx: dict, snippet: Optional[list] = None) -> dict:
        origin, label = origin_of(ev["source_type"])
        out = {"evidence_id": ev.get("evidence_id", ev.get("id")), "lecture_id": ev["lecture_id"],
               "source_type": ev.get("source_type", ev.get("kind")), "origin": origin, "origin_label": label,
               "is_lecture_evidence": is_lecture_evidence(ev.get("source_type", ev.get("kind"))),
               "text": ev["text"], "confidence": ev.get("confidence"), "citation": self._citation(ev, cx)}
        if snippet is not None:
            out["snippet"] = snippet
        return out

    # ── overview ─────────────────────────────────────────────────────────
    def _slide_text_info(self, lecture_id: int, slides: list) -> dict:
        """Which pages have no text, and why, from the stored slides plus the last PROCESS_SLIDES output (OCR detail)."""
        keys = ("ocr_pages", "ocr_unavailable", "ocr_failed", "ocr_low_confidence", "vision_pages", "low_quality_pages",
                "unreadable_pages", "vision_failed", "vision_unreadable", "vision_errors")
        out = {"pages_without_text": [s["idx"] for s in slides if not (s["text"] or "").strip()], "transcribed_by": [],
               "ai_study": None, **{k: [] for k in keys}}
        jobs = self.repo.list_jobs(lecture_id)
        st = self.repo.get_stage(jobs[-1]["id"], "PROCESS_SLIDES") if jobs else None
        o = (st or {}).get("output") or {}
        for k in keys + ("transcribed_by",):
            out[k] = list(o.get(k) or [])
        an = self.repo.get_stage(jobs[-1]["id"], "ANALYZE") if jobs else None
        out["ai_study"] = ((an or {}).get("output") or {}).get("ai_study")
        return out

    @staticmethod
    def _text_warnings(info: dict, n_slides: int, n_evidence: int) -> List[dict]:
        def pages(ix):
            ix = sorted(ix)
            return ", ".join(map(str, ix[:10])) + (f" and {len(ix) - 10} more" if len(ix) > 10 else "")
        out, empty = [], info["pages_without_text"]
        if n_evidence == 0:
            out.append({"code": "no_readable_content",
                        "message": "Notiv found no readable text or speech in this lecture's sources, so the summary, "
                                   "study material and Ask Notiv have nothing to work from."})
        if info.get("low_quality_pages"):
            lq, fixed = sorted(info["low_quality_pages"]), sorted(set(info["low_quality_pages"]) - set(empty))
            out.append({"code": "low_quality_text", "pages": lq,
                        "message": f"{len(lq)} page(s) had an unreliable embedded text layer (garbled or scattered words, page "
                                   f"{pages(lq)}), so it was not used as evidence." +
                                   (f" {len(fixed)} of them were re-read from the page image." if fixed else "")})
        if empty:
            low, unavailable, failed = (set(info[k]) & set(empty) for k in ("ocr_low_confidence", "ocr_unavailable", "ocr_failed"))
            vfail = set(info.get("vision_failed") or []) & set(empty)
            errs = " ".join(info.get("vision_errors") or [])
            cause = ("the AI provider's daily limit was used up" if "daily_limit" in errs else
                     "the AI provider had no credits" if "quota_exhausted" in errs else
                     "the AI provider was rate-limiting" if "rate_limited" in errs else "the AI provider was unavailable")
            why = (f" Reading the page images failed ({cause}); reprocess the lecture later to try again." if vfail else
                   " Text recognition (OCR) isn't available on this server, so scanned pages can't be read." if unavailable else
                   " Text recognition couldn't read them reliably (handwriting or a poor-quality scan?)." if low else
                   " Text recognition failed on them; reprocessing may help." if failed else "")
            out.append({"code": "pages_without_text", "pages": sorted(empty),
                        "message": f"{len(empty)} of {n_slides} page(s) have no readable text (page {pages(empty)}), so "
                                   f"nothing on them can be quoted, summarised or asked about.{why}"})
        if info.get("vision_pages"):
            vp, by = sorted(info["vision_pages"]), ", ".join(info.get("transcribed_by") or []) or "an AI model"
            out.append({"code": "vision_pages", "pages": vp,
                        "message": f"{len(vp)} page(s) were handwritten, scanned or had a broken text layer and were "
                                   f"transcribed from the page image by {by} (page {pages(vp)}); check quotes against the page."})
        ai = info.get("ai_study") or {}
        if ai.get("status") in ("failed", "no_provider"):
            out.append({"code": "ai_study_unavailable", "message": ai.get("message") or "AI study material is unavailable.",
                        "failures": ai.get("failures") or []})
        if info["ocr_pages"]:
            out.append({"code": "ocr_pages", "pages": sorted(info["ocr_pages"]),
                        "message": f"{len(info['ocr_pages'])} page(s) had no text layer and were read with text recognition "
                                   f"(OCR) (page {pages(info['ocr_pages'])}); quotes from them may contain recognition errors."})
        return out

    def overview(self, user_id: int, lecture_id: int) -> dict:
        lec = self._lecture(user_id, lecture_id)
        prog = self._progress(lec)
        out = {"id": lec["id"], "title": lec["title"], "course_id": lec["course_id"],
               "workspace_id": lec["workspace_id"], "language": lec["language"], "duration": lec["duration"],
               "created_at": lec["created_at"], **prog}
        if lec["status"] != "done":
            out.update(ready=False, media=None, inputs=self._inputs(lecture_id), counts=None, warnings=[])
            return out
        cx = self._ctx(lecture_id)
        d = cx["data"]
        ins = self._insights(lecture_id)
        n_ev = len(self.repo.list_evidence(lecture_id))
        counts = {"slides": len(d["slides"]), "segments": len(d["segments"]), "evidence": n_ev,
                  "key_moments": sum(1 for i in ins if i["kind"] == "key_moment"),
                  "summary_sections": sum(1 for i in ins if i["kind"] == "summary_section")}
        ai = ai_study.load(self.repo, lecture_id)
        if ai:
            counts["summary_sections"] = len(ai["sections"])
        warnings = []
        unassigned = sum(1 for s in d["segments"] if s["slide_index"] is None)
        if cx["media"] is None:
            warnings.append({"code": "no_recording", "message": "No recording was uploaded, so nothing can be played or seeked."})
        if cx["deck"] is not None and cx["media"] is not None and cx["media"]["type"] == "audio":
            warnings.append({"code": "audio_only_slides", "message": "Audio can't show which slide is on screen, so transcript lines are not matched to slides."})
        if cx["media"] is not None and not d["segments"]:
            warnings.append({"code": "no_transcript", "message": "The recording produced no transcript text."})
        if d["segments"] and d["slides"] and unassigned and cx["media"] and cx["media"]["type"] == "video":
            warnings.append({"code": "unassigned_segments", "message": f"{unassigned} transcript line(s) could not be matched to a slide."})
        text_info = self._slide_text_info(lecture_id, d["slides"])
        counts.update(pages_without_text=len(text_info["pages_without_text"]), ocr_pages=len(text_info["ocr_pages"]))
        warnings.extend(self._text_warnings(text_info, len(d["slides"]), n_ev))
        out.update(ready=True, counts=counts, warnings=warnings, inputs=self._inputs(lecture_id),
                   media=self._media(cx),
                   mode={"has_video": bool(cx["media"] and cx["media"]["type"] == "video"),
                         "has_audio_only": bool(cx["media"] and cx["media"]["type"] == "audio"),
                         "has_slides": bool(d["slides"]), "has_transcript": bool(d["segments"]),
                         "can_seek": bool(cx["media"])})
        return out

    def _inputs(self, lecture_id: int) -> list:
        return [{"asset_id": a["id"], "type": a["type"], "filename": a["original_filename"],
                 "duration_seconds": a["duration_seconds"], "status": a["status"]} for a in self.repo.list_assets(lecture_id)]

    def _media(self, cx: dict) -> Optional[dict]:
        m = cx["media"]
        return None if not m else {"asset_id": m["id"], "type": m["type"], "mime_type": m["mime_type"],
                                   "duration_seconds": m["duration_seconds"], "filename": m["original_filename"],
                                   "url": self.storage.signed_url(m["storage_key"], MEDIA_URL_TTL),
                                   "url_expires_in": MEDIA_URL_TTL}

    # ── timeline / transcript / slides ───────────────────────────────────
    def timeline(self, user_id: int, lecture_id: int) -> dict:
        self._ready(user_id, lecture_id)
        cx = self._ctx(lecture_id)
        spans = [{**t, "title": insights.slide_title(cx["slide_text"].get(t["slide_index"]), t["slide_index"])
                  if t["slide_index"] is not None else None} for t in cx["data"]["timeline"]]
        moments = [self._moment(m, cx) for m in self._insights(lecture_id) if m["kind"] == "key_moment" and m["start"] is not None]
        return {"lecture_id": lecture_id, "duration": cx["data"]["lecture"]["duration"], "spans": spans,
                "key_moments": [{"id": m["id"], "start": m["start"], "title": m["title"], "reason": m["reason"],
                                 "slide_index": m["slide_index"], "origin": m["origin"]} for m in moments]}

    def transcript(self, user_id: int, lecture_id: int, start: Optional[float] = None, end: Optional[float] = None,
                   slide_index: Optional[int] = None, after_id: int = 0, limit: int = 500) -> dict:
        self._ready(user_id, lecture_id)
        limit = min(max(int(limit), 1), MAX_LIMIT * 5)
        cx = self._ctx(lecture_id)
        out = []
        for s in cx["data"]["segments"]:
            if s["id"] <= after_id and after_id:
                continue
            if start is not None and (s["end"] or 0) < start:
                continue
            if end is not None and s["start"] > end:
                continue
            if slide_index is not None and s["slide_index"] != slide_index:
                continue
            pos = cx["sync"].position_for_segment(s)
            out.append({"segment_id": s["id"], "evidence_id": s["evidence_id"], "start": s["start"], "end": s["end"],
                        "timestamp": mmss(s["start"]), "text": s["text"], "slide_index": s["slide_index"],
                        "straddles_slide_change": bool(s["straddles"]), "confidence": s["confidence"],
                        "slide_manually_set": bool(s["manual_slide"]), "text_edited": bool(s["edited"]),
                        "origin": "professor_said", "origin_label": "Professor said",
                        "citation": {"label": " · ".join(([f"Slide {s['slide_index']}"] if s["slide_index"] is not None else []) + [mmss(s["start"])]),
                                     "seek_seconds": pos["seek_seconds"], "slide_index": pos["slide_index"],
                                     "source": self._source(cx["media"])}})
        page, more = out[:limit], len(out) > limit
        return {"lecture_id": lecture_id, "segments": page, "has_more": more,
                "next_after_id": page[-1]["segment_id"] if more and page else None}

    def slides(self, user_id: int, lecture_id: int) -> dict:
        self._ready(user_id, lecture_id)
        cx = self._ctx(lecture_id)
        ev_by_slide = {e["slide_index"]: e["id"] for e in self.repo.list_evidence(lecture_id) if e["source_type"] == "slide"}
        out = []
        for s in cx["data"]["slides"]:
            i = s["idx"]
            spans = cx["sync"].spans_for_slide(i)
            seek = cx["sync"].seek_for_slide(i)
            out.append({"slide_index": i, "slide_id": s["id"], "title": insights.slide_title(s["text"], i), "text": s["text"] or "",
                        "image_url": self.storage.signed_url(s["image_key"], MEDIA_URL_TTL),
                        "evidence_id": ev_by_slide.get(i), "seek_seconds": seek,
                        "appearances": [{"start": e["start"], "end": e["end"], "confidence": e["confidence"]} for e in spans],
                        "origin": "slide_text", "origin_label": "Slide text",
                        "citation": {"label": " · ".join([f"Slide {i}"] + ([mmss(seek)] if seek is not None else [])),
                                     "seek_seconds": seek, "slide_index": i, "source": self._source(cx["deck"])}})
        return {"lecture_id": lecture_id, "slides": out}

    # ── evidence ─────────────────────────────────────────────────────────
    def evidence(self, user_id: int, lecture_id: int, source_type: Optional[str] = None,
                 slide_index: Optional[int] = None, start: Optional[float] = None, end: Optional[float] = None,
                 after_id: int = 0, limit: int = 100) -> dict:
        self._ready(user_id, lecture_id)
        limit = min(max(int(limit), 1), MAX_LIMIT)
        cx = self._ctx(lecture_id)
        items = []
        for e in self.repo.list_evidence(lecture_id):
            if e["id"] <= after_id:
                continue
            if source_type and e["source_type"] != source_type:
                continue
            if slide_index is not None and e["slide_index"] != slide_index:
                continue
            if start is not None and e["start"] is not None and (e["end"] or e["start"]) < start:
                continue
            if end is not None and e["start"] is not None and e["start"] > end:
                continue
            items.append(e)
        page, more = items[:limit], len(items) > limit
        return {"lecture_id": lecture_id, "items": [self._present(e, cx) for e in page], "has_more": more,
                "next_after_id": page[-1]["id"] if more and page else None}

    def evidence_item(self, user_id: int, lecture_id: int, evidence_id: int) -> dict:
        self._ready(user_id, lecture_id)
        ev = authz.get_evidence(self.repo, user_id, evidence_id)
        if ev["lecture_id"] != lecture_id:
            raise NotFoundError("not found")                  # evidence of another lecture is not addressable here
        cx = self._ctx(lecture_id)
        out = self._present(ev, cx)
        supports = [i for i in self._insights(lecture_id) if evidence_id in i["evidence_ids"]]
        out["supports"] = [{"insight_id": i["id"], "kind": i["kind"], "title": i["title"]} for i in supports]
        return out

    # ── Notiv-generated insights ─────────────────────────────────────────
    def _insights(self, lecture_id: int) -> List[dict]:
        insights.ensure(self.repo, lecture_id)
        return self.repo.list_insights(lecture_id)

    def _moment(self, m: dict, cx: dict, with_evidence: bool = False) -> dict:
        out = {"id": m["id"], "kind": m["kind"], "title": m["title"], "reason": m["reason"], "method": m["method"],
               "origin": "notiv_generated", "origin_label": "Notiv analysis", "start": m["start"], "end": m["end"],
               "slide_index": m["slide_index"],
               "seek_seconds": m["start"] if m["start"] is not None else
               (cx["sync"].seek_for_slide(m["slide_index"]) if m["slide_index"] is not None else None),
               "evidence_ids": m["evidence_ids"]}
        if m["kind"] == "summary_section":
            out["body"] = m.get("body")            # always present on sections (null = slide text only, no excerpts)
        if with_evidence:
            by_id = {e["id"]: e for e in self.repo.list_evidence(m["lecture_id"])}
            out["evidence"] = [self._present(by_id[i], cx) for i in m["evidence_ids"] if i in by_id]
        return out

    def key_moments(self, user_id: int, lecture_id: int) -> dict:
        self._ready(user_id, lecture_id)
        cx = self._ctx(lecture_id)
        ms = [m for m in self._insights(lecture_id) if m["kind"] == "key_moment"]
        return {"lecture_id": lecture_id, "origin": "notiv_generated",
                "note": "Key moments are selected by Notiv from the transcript and slides; each links to the lecture evidence it came from.",
                "key_moments": [self._moment(m, cx, with_evidence=True) for m in ms]}

    def summary(self, user_id: int, lecture_id: int) -> dict:
        self._ready(user_id, lecture_id)
        cx = self._ctx(lecture_id)
        ai = ai_study.load(self.repo, lecture_id)
        if ai:
            return self._ai_summary(lecture_id, ai, cx)
        ss = [m for m in self._insights(lecture_id) if m["kind"] == "summary_section"]
        return {"lecture_id": lecture_id, "origin": "notiv_generated", "mode": "extractive",
                "note": "Sections are assembled by Notiv from verbatim lecture excerpts; open any supporting evidence to see it in context.",
                "sections": [self._moment(m, cx, with_evidence=True) for m in ss]}

    def _ai_summary(self, lecture_id: int, ai: dict, cx: dict) -> dict:
        """Same section shape as the extractive summary (so every client keeps working), written by the answer model and
        cited: each section/takeaway carries the evidence it was written from; pages come from that evidence only."""
        by_id = {e["id"]: e for e in self.repo.list_evidence(lecture_id)}

        def pres(ids):
            return [self._present(by_id[i], cx) for i in ids if i in by_id]

        def seek(ids):
            e = next((by_id[i] for i in ids if i in by_id), None)
            if not e:
                return None, None
            return e.get("slide_index"), (e["start"] if e.get("start") is not None else
                                          cx["sync"].seek_for_slide(e["slide_index"]) if e.get("slide_index") is not None else None)
        def span(ids):                                       # the recording moment the section was written from
            evs = [by_id[i] for i in ids if i in by_id and by_id[i].get("start") is not None]
            return (min(e["start"] for e in evs), max(e["end"] if e.get("end") is not None else e["start"] for e in evs)) if evs else (None, None)
        sections = []
        for n, sec in enumerate(ai["sections"]):
            slide, sk = seek(sec["evidence_ids"])
            start, end = span(sec["evidence_ids"])
            sections.append({"id": -(n + 1), "kind": "summary_section", "title": sec["title"], "reason": None, "method": "ai:summary",
                             "origin": "ai_generated", "origin_label": "AI summary of your source", "start": start, "end": end,
                             "slide_index": slide, "seek_seconds": start if start is not None else sk,
                             "evidence_ids": sec["evidence_ids"], "pages": sec["pages"], "times": sec.get("times") or [],
                             "body": sec["summary"], "evidence": pres(sec["evidence_ids"])})
        return {"lecture_id": lecture_id, "origin": "ai_generated", "mode": "ai", "provider": ai["provider"], "model": ai["model"],
                "generated_at": ai["generated_at"],
                "note": f"Written by {ai['provider']}/{ai['model']} from this lecture's evidence. Every section cites the "
                        "pages it was written from; open the evidence to check it. The raw extracted text is in the Evidence tab.",
                "title": ai.get("title"), "overview": ai.get("overview"),
                "takeaways": [{**t, "evidence": pres(t["evidence_ids"])} for t in ai.get("takeaways") or []],
                "topics": ai.get("topics") or [], "comparisons": ai.get("comparisons") or [], "sections": sections}

    def mind_map(self, user_id: int, lecture_id: int) -> dict:
        """Concept map for the notebook canvas: topic nodes, concept nodes and labelled edges (topic → concept 'covers',
        plus the relations the source establishes). Every node and edge carries its pages / evidence ids."""
        self._ready(user_id, lecture_id)
        ai = ai_study.load(self.repo, lecture_id)
        if not ai:
            return {"nodes": [], "edges": [], "insufficient_evidence": True, "mode": "unavailable",
                    "message": "A connected concept map needs AI study material for this lecture. Configure an AI provider "
                               "and regenerate, or reprocess the lecture."}
        concepts = {c["name"]: c for c in ai["concepts"]}
        nodes, edges, ids = [], [], {}
        placed = set()
        for t_i, t in enumerate(ai.get("topics") or []):
            tid = f"topic-{t_i}"
            nodes.append({"id": tid, "text": t["name"], "kind": "topic", "x": 30 + t_i * 300, "y": 30,
                          "source": {"lecture_id": lecture_id, "pages": t["pages"], "times": t.get("times") or [], "evidence_ids": t["evidence_ids"][:8]}})
            for c_i, name in enumerate(t["concepts"]):
                if name not in concepts:
                    continue
                if name not in ids:
                    c = concepts[name]
                    ids[name] = f"concept-{len(ids)}"
                    nodes.append({"id": ids[name], "text": name, "kind": "concept", "x": 30 + t_i * 300, "y": 150 + c_i * 90,
                                  "source": {"lecture_id": lecture_id, "pages": c["pages"], "times": c.get("times") or [],
                                             "evidence_ids": c["mention_evidence_ids"][:8]}})
                    placed.add(name)
                edges.append({"from": tid, "to": ids[name], "label": "covers"})
        extra = [c for c in ai["concepts"] if c["name"] not in placed and any(c["name"] in (r["source"], r["target"]) for r in ai.get("relations") or [])]
        for n_i, c in enumerate(extra):
            ids[c["name"]] = f"concept-{len(ids)}"
            nodes.append({"id": ids[c["name"]], "text": c["name"], "kind": "concept", "x": 30 + (n_i % 4) * 300,
                          "y": 150 + (10 + n_i // 4) * 90,
                          "source": {"lecture_id": lecture_id, "pages": c["pages"], "times": c.get("times") or [],
                                     "evidence_ids": c["mention_evidence_ids"][:8]}})
        for r in ai.get("relations") or []:
            if r["source"] in ids and r["target"] in ids:
                edges.append({"from": ids[r["source"]], "to": ids[r["target"]], "label": r["label"],
                              "source": {"lecture_id": lecture_id, "pages": r["pages"], "times": r.get("times") or [], "evidence_ids": r["evidence_ids"]}})
        return {"nodes": nodes[:120], "edges": [e for e in edges if e["from"] in {n["id"] for n in nodes[:120]} and
                                                 e["to"] in {n["id"] for n in nodes[:120]}],
                "mode": "ai", "provider": ai["provider"], "model": ai["model"]}

    # ── search ───────────────────────────────────────────────────────────
    def search(self, user_id: int, lecture_id: int, q: str, limit: int = 20, source_type: Optional[str] = None) -> dict:
        self._ready(user_id, lecture_id)
        q = (q or "").strip()
        if not q:
            raise ValidationError("query required")
        if len(q) > MAX_QUERY:
            raise ValidationError(f"query too long (max {MAX_QUERY} characters)")
        if source_type not in (None, "transcript", "slide"):
            raise ValidationError("source_type must be 'transcript' or 'slide'")
        limit = min(max(int(limit), 1), 100)
        mode = "all"
        rows = self.repo.search_evidence([lecture_id], q, limit=limit * 3 if source_type else limit, match="all")
        if not rows and len(self._terms(q)) > 1:
            rows, mode = self.repo.search_evidence([lecture_id], q, limit=limit * 3 if source_type else limit, match="any"), "any"
        if source_type:
            rows = [r for r in rows if r["kind"] == source_type][:limit]
        cx = self._ctx(lecture_id)
        rx = self._highlighter(q)
        results = []
        for r in rows:
            ev = {**r, "source_type": r["kind"]}
            results.append(self._present(ev, cx, snippet=self._snippet(r["text"], rx)))
        return {"lecture_id": lecture_id, "query": q, "match": mode, "count": len(results), "results": results}

    @staticmethod
    def _terms(q: str) -> List[str]:
        return [t for t in re.findall(r"\w+", q, re.U)]

    def _highlighter(self, q: str):
        terms = sorted({re.escape(t) for t in self._terms(q)}, key=len, reverse=True)
        return re.compile(r"\b(" + "|".join(terms) + r")\w*", re.I | re.U) if terms else None

    @staticmethod
    def _snippet(text: str, rx, width: int = 160) -> List[dict]:
        """[{text, match}] parts around the first hit — structured, so clients
        never render raw HTML from lecture text."""
        m = rx.search(text) if rx else None
        a = max(0, (m.start() if m else 0) - width // 3)
        b = min(len(text), a + width)
        window = text[a:b]
        parts, pos = [], 0
        for h in (rx.finditer(window) if rx else []):
            if h.start() > pos:
                parts.append({"text": window[pos:h.start()], "match": False})
            parts.append({"text": h.group(0), "match": True})
            pos = h.end()
        if pos < len(window):
            parts.append({"text": window[pos:], "match": False})
        if a > 0:
            parts.insert(0, {"text": "…", "match": False})
        if b < len(text):
            parts.append({"text": "…", "match": False})
        return parts

    def present_evidence(self, lecture_id: int, evidence_ids: List[int]) -> List[dict]:
        """Evidence items in the standard citation shape for ids of ONE lecture. Internal: the caller must
        already have authorized `lecture_id`. Ids that aren't evidence of this lecture are silently absent."""
        cx = self._ctx(lecture_id)
        by_id = {e["id"]: e for e in self.repo.list_evidence(lecture_id)}
        return [self._present(by_id[i], cx) for i in evidence_ids if i in by_id]

    # ── position sync ────────────────────────────────────────────────────
    def locate(self, user_id: int, lecture_id: int, t: Optional[float] = None, slide_index: Optional[int] = None,
               segment_id: Optional[int] = None, evidence_id: Optional[int] = None) -> dict:
        """Resolves exactly one of time / slide / segment / evidence to the
        full synchronized position {seek_seconds, slide_index, segment_id}."""
        given = [x for x in (t, slide_index, segment_id, evidence_id) if x is not None]
        if len(given) != 1:
            raise ValidationError("give exactly one of: t, slide, segment, evidence")
        self._ready(user_id, lecture_id)
        cx = self._ctx(lecture_id)
        sync = cx["sync"]
        if t is not None:
            if t < 0:
                raise ValidationError("t must be >= 0")
            dur = cx["data"]["lecture"]["duration"]
            return sync.position_at(min(t, dur) if dur else t)
        if slide_index is not None:
            if not any(s["idx"] == slide_index for s in cx["data"]["slides"]):
                raise NotFoundError("not found")
            return sync.position_for_slide(slide_index)
        if segment_id is not None:
            seg = next((s for s in cx["data"]["segments"] if s["id"] == segment_id), None)
            if not seg:
                raise NotFoundError("not found")
            return sync.position_for_segment(seg)
        ev = authz.get_evidence(self.repo, user_id, evidence_id)
        if ev["lecture_id"] != lecture_id:
            raise NotFoundError("not found")
        cit = self._citation(ev, cx)
        seg = sync.segment_at(cit["seek_seconds"]) if cit["seek_seconds"] is not None else None
        return {"seek_seconds": cit["seek_seconds"], "slide_index": ev["slide_index"],
                "segment_id": ev["source_id"] if ev["source_type"] == "transcript" else (seg["id"] if seg else None)}
