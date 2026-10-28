"""Notiv-generated lecture insights, built ONLY from stored lecture evidence.

Deterministic and extractive (no LLM, no invented text):

  key moments      · slide changes — each slide's first appearance in the recording
                   · professor emphasis — transcript segments containing an explicit
                     emphasis cue ("important", "remember", "exam", ...)
  summary sections one per contiguous slide span (or ~5-minute window when there
                   are no slides): the slide title plus the transcript segments that
                   best cover that slide's vocabulary, quoted verbatim

Every insight stores the evidence ids it was built from; the summary text is
those evidence excerpts, so nothing in it can be unsupported. Insights are
stored separately from evidence (never indexed as lecture content, never fed to
grounded Q&A) and are regenerated when the lecture's evidence/timeline changes
(fingerprint), including after student corrections.

An abstractive (LLM) summary can be layered on later by writing additional
`method` values; the storage contract (insight -> evidence ids) is the same.
"""
import hashlib
import re
from typing import List, Optional

from .repo import LectureRepository

METHOD_SLIDE = "rule:slide_change"
METHOD_EMPHASIS = "rule:emphasis_cue"
METHOD_SUMMARY = "extractive:slide_span"
METHOD_SUMMARY_WINDOW = "extractive:time_window"

MAX_EMPHASIS = 12
EMPHASIS_DEDUPE_SECONDS = 8.0     # suppress adjacent restatements only; transcript lines are ~3-10 s
WINDOW_SECONDS = 300.0
MAX_EXCERPTS = 3
TITLE_MAX = 90

_CUES = [  # (pattern, label) — explicit, auditable cues; first match names the reason
    (r"\bexam(s)?\b|\bquiz(zes)?\b|\bmidterm\b|\bfinal\b.*\btest\b|\bwill be on\b", "exam relevance"),
    (r"\bremember\b|\bdon'?t forget\b|\bkeep in mind\b|\bmake sure\b|\bnote that\b", "reminder"),
    (r"\bimportant(ly)?\b|\bcrucial(ly)?\b|\bkey (idea|point|takeaway|insight|concept)\b|\bmost important\b|\bcritical\b", "emphasis"),
    (r"\bin summary\b|\bto summari[sz]e\b|\bthe main (idea|point)\b|\bthe takeaway\b|\bto recap\b", "summary cue"),
    (r"\bis defined as\b|\bwe define\b|\bdefinition\b", "definition"),
]
_CUE_RE = [(re.compile(p, re.I), label) for p, label in _CUES]
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]{2,}")
_STOP = frozenset("the and for that this with are was were you your from have has will can not but all any one our "
                  "about into over than then them they their there here what when which who how why its it's".split())


def slide_title(text: Optional[str], index: int) -> str:
    for line in (text or "").splitlines():
        line = " ".join(line.split())
        if line:
            return line[:TITLE_MAX]
    return f"Slide {index}"


def _shorten(text: str, n: int = TITLE_MAX) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"


def _terms(text: str) -> set:
    return {w.lower() for w in _WORD.findall(text or "") if w.lower() not in _STOP}


def fingerprint(repo: LectureRepository, lecture_id: int) -> str:
    h = hashlib.sha1()
    for e in repo.list_evidence(lecture_id):
        h.update(repr((e["id"], e["source_type"], e["start"], e["end"], e["slide_index"], e["text"])).encode())
    for t in repo.timeline_rows(lecture_id):
        h.update(repr((t["slide_index"], t["start"], t["end"])).encode())
    return h.hexdigest()


def build(repo: LectureRepository, lecture_id: int) -> List[dict]:
    """Pure-ish: reads evidence/timeline, returns insight records (not stored)."""
    evidence = repo.list_evidence(lecture_id)
    transcript = sorted((e for e in evidence if e["source_type"] == "transcript"), key=lambda e: e["start"] or 0)
    slide_ev = {e["slide_index"]: e for e in evidence if e["source_type"] == "slide"}
    timeline = [t for t in repo.timeline_rows(lecture_id)]
    n_slides = repo.slide_count(lecture_id)
    slide_text = {i: (slide_ev[i]["text"] if i in slide_ev else (repo.get_slide(lecture_id, i) or {}).get("text"))
                  for i in range(1, n_slides + 1)}
    recs: List[dict] = []

    # ── sections (contiguous slide spans, or time windows without slides) ───
    spans = [t for t in timeline if t["slide_index"] is not None]
    sections: List[dict] = []
    if spans:
        for t in spans:                                 # merge consecutive spans of the same slide
            if sections and sections[-1]["slide_index"] == t["slide_index"] and abs(sections[-1]["end"] - t["start"]) < 1.0:
                sections[-1]["end"] = t["end"]
            else:
                sections.append(dict(slide_index=t["slide_index"], start=t["start"], end=t["end"]))
    elif transcript:
        last = max((e["end"] or e["start"] or 0) for e in transcript)
        s0 = 0.0
        while s0 < last:
            sections.append(dict(slide_index=None, start=s0, end=min(s0 + WINDOW_SECONDS, last)))
            s0 += WINDOW_SECONDS
    elif slide_ev:
        sections = [dict(slide_index=i, start=None, end=None) for i in sorted(slide_ev)]
    for sec in sections:
        sec["lines"] = []

    # A transcript line belongs to the section of its ALIGNED slide (this honours student corrections);
    # a revisited slide's line goes to the span containing it, else the slide's first section.
    # Lines with no aligned slide fall back to time containment.
    for e in transcript:
        if e["start"] is None:
            continue
        if spans and e["slide_index"] is not None:
            cands = [x for x in sections if x["slide_index"] == e["slide_index"]]
            home = next((x for x in cands if x["start"] <= e["start"] < x["end"]), cands[0] if cands else None)
        else:
            home = next((x for x in sections if x["start"] is not None and x["start"] <= e["start"] < x["end"]), None)
        if home is not None:
            home["lines"].append(e)

    # ── key moments: slide changes ───────────────────────────────────────
    seen = set()
    for sec in sections:
        i = sec["slide_index"]
        if not spans or i is None or i in seen:
            continue
        seen.add(i)
        first_say = sec["lines"][0] if sec["lines"] else None
        ev = ([slide_ev[i]["id"]] if i in slide_ev else []) + ([first_say["id"]] if first_say else [])
        if not ev:
            continue                                   # a moment with no supporting evidence is not shown
        recs.append(dict(kind="key_moment", title=slide_title(slide_text.get(i), i),
                         reason=f"Slide {i} first appears", method=METHOD_SLIDE,
                         start=sec["start"], end=sec["end"], slide_index=i, evidence_ids=ev))
    if not timeline:                                   # slides-only lecture: no times, ordered by slide
        for i, e in sorted(slide_ev.items()):
            recs.append(dict(kind="key_moment", title=slide_title(e["text"], i), reason=f"Slide {i}",
                             method=METHOD_SLIDE, start=None, end=None, slide_index=i, evidence_ids=[e["id"]]))

    # ── key moments: professor emphasis ──────────────────────────────────
    cand = []
    for e in transcript:
        hits = [label for rx, label in _CUE_RE if rx.search(e["text"])]
        if hits:
            cand.append((len(hits), e, hits[0]))
    cand.sort(key=lambda c: (-c[0], c[1]["start"] or 0))
    chosen: List[tuple] = []
    for score, e, label in cand:
        if len(chosen) >= MAX_EMPHASIS:
            break
        if all(abs((e["start"] or 0) - (o["start"] or 0)) >= EMPHASIS_DEDUPE_SECONDS for _, o, _ in chosen):
            chosen.append((score, e, label))
    for _, e, label in chosen:
        recs.append(dict(kind="key_moment", title=_shorten(e["text"]), reason=f"Professor emphasis ({label})",
                         method=METHOD_EMPHASIS, start=e["start"], end=e["end"], slide_index=e["slide_index"],
                         evidence_ids=[e["id"]]))
    recs.sort(key=lambda r: (r["start"] is None, r["start"] or 0, r["slide_index"] or 0))

    # ── summary sections ─────────────────────────────────────────────────
    for sec in sections:
        i, a, b = sec["slide_index"], sec["start"], sec["end"]
        inside = sec["lines"]
        vocab = _terms(slide_text.get(i) or "") if i else set()
        ranked = sorted(inside, key=lambda e: (-len(_terms(e["text"]) & vocab), -len(e["text"]), e["start"]))
        picked = sorted(ranked[:MAX_EXCERPTS], key=lambda e: e["start"])
        ev_ids = ([slide_ev[i]["id"]] if i in slide_ev else []) + [e["id"] for e in picked]
        if not ev_ids:
            continue
        if i:
            title, method = slide_title(slide_text.get(i), i), METHOD_SUMMARY
        else:
            title, method = f"{int(a // 60)}–{int(b // 60) + (1 if b % 60 else 0)} min", METHOD_SUMMARY_WINDOW
        recs.append(dict(kind="summary_section", title=title, body=" … ".join(e["text"] for e in picked) or None,
                         reason=f"{len(picked)} of {len(inside)} transcript segments quoted" if inside else
                                "slide text only (no transcript in this span)",
                         method=method, start=a, end=b, slide_index=i, evidence_ids=ev_ids))
    return recs


def ensure(repo: LectureRepository, lecture_id: int, force: bool = False) -> bool:
    """Generates insights if missing or stale. Returns True if (re)generated."""
    fp = fingerprint(repo, lecture_id)
    if not force and repo.insight_fingerprint(lecture_id) == fp:
        return False
    repo.replace_insights(lecture_id, build(repo, lecture_id), fp)
    return True
