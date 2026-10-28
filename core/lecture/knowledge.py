"""Knowledge extraction: concepts, definitions, formulas, facts, examples, emphasis and warnings — built ONLY from
a lecture's stored evidence (transcript segments + slide text).

Deterministic and extractive. Every item is anchored on a VERBATIM sentence (speech, or written prose on a slide / document page) or line (slide) of real
evidence; items store that quote with the evidence id, and the store refuses any link whose quote is not literally
contained in the evidence. Titles are either a term cut from the quoted sentence or a slide title — nothing is
generated from outside the lecture, so nothing can be invented.

   definition  "X is defined as …", "X means …", "X happens when …", "X is a/an/the …" (the plain copular form needs
               X to be a slide title or to recur, so ordinary sentences aren't mistaken for definitions)
   formula     slide lines with "=" ; spoken "… equals …"
   warning     "be careful", "common mistake", "avoid", "never" …
   example     "for example", "for instance", "such as", "suppose" …
   emphasis    explicit cues: important / remember / exam / key point / in summary …
   fact        a sentence with a number or an explicit causal cue (because, reduces, leads to …)
   concept     each slide title and each defined term, linked to the evidence that mentions it

One sentence can yield an emphasis AND a fact item, but never both a definition and anything else.
Regeneration is a keyed sync (see study_store): user deletions stick, review progress survives.
"""
import hashlib
import re
from typing import Dict, List, Optional, Tuple

from . import ai_study, insights, retrieval
from .repo import LectureRepository

METHOD = "extract:v2"          # v2: written prose on slides / document pages is mined like speech
CAPS = {"concept": 40, "definition": 40, "formula": 20, "fact": 30, "example": 20, "emphasis": 20, "warning": 15}
KINDS = tuple(CAPS)

_ART = r"(?:(?:a|an|the)\s+)?"
_LEAD = r"^(?:(?:so|and|now|then|well|okay|ok|but|also),?\s+)?"
_TERM = r"(?P<t>[^,.;:!?]{2,50}?)"
_EXPLICIT = [re.compile(_LEAD + _ART + _TERM + p, re.I) for p in (
    r"\s+(?:is|are)\s+defined\s+as\s+(?P<d>.+)$",
    r"\s+(?:means|refers to|stands for)\s+(?P<d>.+)$",
    r"\s+(?:happens|occurs|arises)\s+when\s+(?P<d>.+)$")]
_COPULAR = re.compile(_LEAD + _ART + r"(?P<t>[^,.;:!?]{2,40}?)\s+(?:is|are)\s+(?P<d>(?:a|an|the)\s+.+)$", re.I)
_BAD_FIRST = frozenset("this that it there here which what how why who we you they i he she one another these those also but "
                       "if when so then because since while as for in on at of to be".split())
_VAGUE = re.compile(r"^(?:a\s+(?:bit|lot|little|few|good|great|nice|big|small|kind of|sort of)|the\s+(?:most|same|only|best|first|last|next|other|rest|way))\b", re.I)
_FORMULA_LINE = re.compile(r"[A-Za-z0-9_\)\]|]\s*=\s*\S")
_SPOKEN_FORMULA = re.compile(r"\b[a-zA-Z]\b\s+equals\b|\bequals\b.*\b(?:times|minus|plus|divided by|over|squared)\b", re.I)
_WARN = re.compile(r"\b(?:be careful|careful|common mistakes?|mistakes?|pitfalls?|watch out|beware|avoid|never|warning|caution|danger|trap|don'?t|do not)\b", re.I)
_EXAMPLE = re.compile(r"\b(?:for example|for instance|e\.g\.|such as|consider|suppose|imagine|let'?s say|let us say)\b", re.I)
_CAUSAL = re.compile(r"\b(?:because|therefore|which means|results? in|leads? to|causes?|ensures?|prevents?|reduces?|increases?|so that|as a result)\b", re.I)
_DIGIT = re.compile(r"\d")
_EMPHASIS = [(rx, lab) for rx, lab in insights._CUE_RE if lab != "definition"]
_WS = re.compile(r"\s+")


def key_of(text: str) -> str:
    return " ".join(retrieval.stem(w) for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", text.lower()))


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def parse_definition(sentence: str) -> Optional[Tuple[str, str, bool]]:
    """(term, definition clause, explicit) if the sentence is a definitional statement, else None."""
    s = sentence.strip().rstrip(".!?")
    for explicit, rx in [(True, r) for r in _EXPLICIT] + [(False, _COPULAR)]:
        m = rx.match(s)
        if not m:
            continue
        term = re.sub(r"^(?:a|an|the)\s+", "", m.group("t").strip(), flags=re.I)
        words = term.split()
        d = m.group("d").strip()
        if not words or len(words) > 4 or words[0].lower() in _BAD_FIRST or all(w.lower() in retrieval.STOP for w in words):
            continue
        if len(d.split()) < 4 or (not explicit and _VAGUE.match(d)):
            continue
        return term, d, explicit
    return None


_PROSE_END = re.compile(r"[.!?]['\"”’)\]]*$")
PROSE_MIN_WORDS = 8


def _is_prose(line: str) -> bool:
    """A written sentence (or paragraph), as opposed to a slide title, bullet fragment or formula line."""
    return len(line.split()) >= PROSE_MIN_WORDS and bool(_PROSE_END.search(line.strip()))


def _sentences(ev: dict) -> List[str]:
    if ev["source_type"] == "slide":
        return [ln.strip(" -•\t") for ln in (ev["text"] or "").splitlines() if ln.strip(" -•\t")]
    return retrieval.split_sentences(ev["text"])


def _dkey(kind: str, anchor: str) -> str:
    return hashlib.sha1(f"{kind}|{' '.join(anchor.lower().split())}".encode()).hexdigest()[:16]


def fingerprint(repo: LectureRepository, lecture_id: int) -> str:
    h = hashlib.sha1(METHOD.encode())
    for e in repo.list_evidence(lecture_id):
        h.update(repr((e["id"], e["source_type"], e["slide_index"], e["text"])).encode())
    ai = ai_study.load(repo, lecture_id)
    if ai:                                                    # AI study material replaces the rule-based extraction
        h.update(("ai:" + ai["fingerprint"]).encode())
    return h.hexdigest()


def ai_records(ai: dict, evidence: List[dict]) -> List[dict]:
    """Knowledge records from the validated AI artifact. Every record links to real evidence with a literal quote (the
    store re-verifies both). `body` of a concept is only ever the SOURCE-supported definition; the supplementary
    explanation is not stored as knowledge (it is served next to it, labelled — see study.StudyService)."""
    by_id = {e["id"]: e for e in evidence}

    def first(ids):
        e = next((by_id[i] for i in ids if i in by_id), None)
        return e or {}

    recs: List[dict] = []
    for c in ai.get("concepts") or []:
        ids = [i for i in c["mention_evidence_ids"] if i in by_id]
        if not ids:
            continue
        e0 = first(ids)
        recs.append({"kind": "concept", "title": _cap(c["name"]), "body": c["definition"] or None, "concept_key": c["key"],
                     "slide_index": e0.get("slide_index"), "start": e0.get("start"), "end": e0.get("end"),
                     "method": "ai:concept" if c["definition"] else "ai:concept_named_only",
                     "confidence": 0.8 if c["definition"] else 0.5, "dedupe_key": _dkey("concept", c["key"]),
                     "evidence": ai_study.links(ids[:4], by_id, c["name"], "primary")})
        dids = [i for i in c["definition_evidence_ids"] if i in by_id]
        if c["definition"] and dids:
            d0 = first(dids)
            recs.append({"kind": "definition", "title": _cap(c["name"]), "body": c["definition"], "concept_key": c["key"],
                         "slide_index": d0.get("slide_index"), "start": d0.get("start"), "end": d0.get("end"),
                         "method": "ai:source_definition", "confidence": 0.8, "dedupe_key": _dkey("definition", c["key"]),
                         "evidence": ai_study.links(dids[:4], by_id, c["name"], "primary")})
    for x in ai.get("examples") or []:
        ids = [i for i in x["evidence_ids"] if i in by_id]
        if ids:
            e0 = first(ids)
            recs.append({"kind": "example", "title": "Example: " + _short(x["concept"] or x["text"], 60), "body": x["text"],
                         "concept_key": ai_study.concept_key(x["concept"]) or None, "slide_index": e0.get("slide_index"),
                         "start": e0.get("start"), "end": e0.get("end"), "method": "ai:example", "confidence": 0.7,
                         "dedupe_key": _dkey("example", x["text"]), "evidence": ai_study.links(ids[:3], by_id, x["concept"])})
    for x in ai.get("comparisons") or []:
        ids = [i for i in x["evidence_ids"] if i in by_id]
        if ids:
            e0, title = first(ids), f"{x['a']} vs {x['b']}"
            recs.append({"kind": "fact", "title": _cap(title), "body": "; ".join(x["points"]), "concept_key": None,
                         "slide_index": e0.get("slide_index"), "start": e0.get("start"), "end": e0.get("end"),
                         "method": "ai:comparison", "confidence": 0.7, "dedupe_key": _dkey("comparison", title),
                         "evidence": ai_study.links(ids[:3], by_id, x["a"])})
    seen, out = set(), []
    for r in recs:
        if r["dedupe_key"] not in seen:
            seen.add(r["dedupe_key"])
            out.append(r)
    return out


def build(repo: LectureRepository, lecture_id: int) -> List[dict]:
    evidence = repo.list_evidence(lecture_id)
    ai = ai_study.load(repo, lecture_id)
    if ai:
        return ai_records(ai, evidence)
    units: List[dict] = []
    for e in sorted(evidence, key=lambda x: (x["slide_index"] if x["slide_index"] is not None else 9999, x["start"] if x["start"] is not None else 0, x["id"])):
        for n, s in enumerate(_sentences(e)):
            units.append({"ev": e["id"], "src": e["source_type"], "text": s, "slide": e["slide_index"], "start": e["start"],
                          "end": e["end"], "line": n})
    slide_title: Dict[int, dict] = {}                         # slide index -> {title, ev, key}
    for u in units:
        if u["src"] == "slide" and u["line"] == 0 and u["slide"] is not None and u["slide"] not in slide_title \
                and not _is_prose(u["text"]):                 # a document page that opens with a paragraph has no title
            slide_title[u["slide"]] = {"title": u["text"], "ev": u["ev"], "key": key_of(u["text"])}
    title_keys = {t["key"] for t in slide_title.values()}
    speech = [u for u in units if u["src"] == "transcript"]
    # Written prose on slides / document pages is read like speech: one unit per sentence (each a literal substring of
    # the evidence). Titles, bullet fragments and formula lines are not prose and keep their line-based handling.
    prose = speech + [{**u, "text": s} for u in units if u["src"] == "slide" and _is_prose(u["text"])
                      and not (u["line"] == 0 and u["slide"] in slide_title) for s in retrieval.split_sentences(u["text"])]
    stems = {id(u): [retrieval.stem(w) for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", u["text"].lower())] for u in speech}

    def mentions(key: str, pool: List[dict]) -> List[dict]:
        kt = key.split()
        out = []
        for u in pool:
            st = stems.get(id(u)) or [retrieval.stem(w) for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", u["text"].lower())]
            if kt and any(st[i:i + len(kt)] == kt for i in range(len(st) - len(kt) + 1)):
                out.append(u)
        return out

    def link(u: dict, role: str) -> dict:
        return {"evidence_id": u["ev"], "quote": u["text"], "role": role}

    def slide_concept(u: dict) -> Optional[str]:
        t = slide_title.get(u["slide"]) if u["slide"] is not None else None
        return t["key"] if t else None

    recs: List[dict] = []
    defs: Dict[str, dict] = {}                                # concept_key -> definition record
    claimed = set()                                           # (ev, text) sentences already turned into a definition

    for u in prose:
        p = parse_definition(u["text"])
        if not p:
            continue
        term, d, explicit = p
        k = key_of(term)
        supported = explicit or k in title_keys or len(mentions(k, prose)) >= 2
        if not supported or k in defs:
            continue
        claimed.add((u["ev"], u["text"]))
        defs[k] = {"kind": "definition", "title": _cap(term), "body": u["text"], "concept_key": k, "slide_index": u["slide"],
                   "start": u["start"], "end": u["end"], "method": METHOD + ":explicit" if explicit else METHOD + ":copular",
                   "confidence": 0.9 if explicit else 0.75, "dedupe_key": _dkey("definition", u["text"]),
                   "evidence": [link(u, "primary")]}
    recs += list(defs.values())

    # formulas: slide lines with "=" and spoken "equals" statements
    for u in units:
        if (u["ev"], u["text"]) in claimed:
            continue
        if u["src"] == "slide" and u["line"] > 0 and len(u["text"]) <= 140 and _FORMULA_LINE.search(u["text"]):
            label = u["text"].split(":", 1)[0].strip() if ":" in u["text"].split("=", 1)[0] else u["text"].split("=", 1)[0].strip()
            recs.append({"kind": "formula", "title": _cap(label[:60]) or "Formula", "body": u["text"], "concept_key": slide_concept(u),
                         "slide_index": u["slide"], "start": None, "end": None, "method": METHOD + ":slide_line", "confidence": 0.85,
                         "dedupe_key": _dkey("formula", u["text"]), "evidence": [link(u, "primary")]})
            claimed.add((u["ev"], u["text"]))
        elif u["src"] == "transcript" and _SPOKEN_FORMULA.search(u["text"]):
            recs.append({"kind": "formula", "title": "Spoken formula", "body": u["text"], "concept_key": slide_concept(u),
                         "slide_index": u["slide"], "start": u["start"], "end": u["end"], "method": METHOD + ":spoken", "confidence": 0.6,
                         "dedupe_key": _dkey("formula", u["text"]), "evidence": [link(u, "primary")]})
            claimed.add((u["ev"], u["text"]))

    # warnings / examples / emphasis / facts from speech and written prose
    for u in prose:
        if (u["ev"], u["text"]) in claimed:
            continue
        t = u["text"]
        kinds: List[Tuple[str, str, float]] = []
        if _WARN.search(t):
            kinds.append(("warning", "Warning: " + _short(t), 0.7))
        elif _EXAMPLE.search(t):
            kinds.append(("example", "Example: " + _short(t), 0.7))
        if not kinds:
            for rx, lab in _EMPHASIS:
                if rx.search(t):
                    kinds.append(("emphasis", lab.capitalize() + ": " + _short(t), 0.6))
                    break
            if _DIGIT.search(t):
                kinds.append(("fact", _short(t), 0.6))
            elif _CAUSAL.search(t):
                kinds.append(("fact", _short(t), 0.5))
        for kind, title, conf in kinds:
            recs.append({"kind": kind, "title": title, "body": t, "concept_key": slide_concept(u), "slide_index": u["slide"],
                         "start": u["start"], "end": u["end"], "method": f"{METHOD}:{kind}", "confidence": conf,
                         "dedupe_key": _dkey(kind, t), "evidence": [link(u, "primary")]})

    # concepts: slide titles + defined terms, each linked to evidence that really mentions them
    concepts: Dict[str, dict] = {}
    for si, t in sorted(slide_title.items()):
        title_u = next(u for u in units if u["ev"] == t["ev"] and u["line"] == 0)
        concepts[t["key"]] = {"title": t["title"], "slide": si, "links": [link(title_u, "primary")], "def": None}
    for k, dr in defs.items():
        c = concepts.setdefault(k, {"title": dr["title"], "slide": dr["slide_index"], "links": [], "def": None})
        c["def"] = dr
        c["links"].insert(0, dict(dr["evidence"][0], role="primary")) if not any(x["evidence_id"] == dr["evidence"][0]["evidence_id"] for x in c["links"]) else None
    for k, c in concepts.items():
        have = {x["evidence_id"] for x in c["links"]}
        pool = [u for u in prose if c["slide"] is None or u["slide"] == c["slide"]] or prose
        extra = [u for u in mentions(k, pool) if u["ev"] not in have][:2]
        c["links"] += [link(u, "support") for u in extra]
        body = c["def"]["body"] if c["def"] else (extra[0]["text"] if extra else None)
        first_speech = next((x for x in c["links"] if x["quote"] and x["evidence_id"] in {u["ev"] for u in speech}), None)
        src = next((u for u in speech if first_speech and u["ev"] == first_speech["evidence_id"] and u["text"] == first_speech["quote"]), None)
        recs.append({"kind": "concept", "title": _cap(c["title"]), "body": body, "concept_key": k, "slide_index": c["slide"],
                     "start": src["start"] if src else None, "end": src["end"] if src else None,
                     "method": METHOD + (":defined" if c["def"] else ":slide_title"), "confidence": 0.8 if c["def"] else 0.6,
                     "dedupe_key": _dkey("concept", k), "evidence": c["links"]})

    out, per, seen = [], {k: 0 for k in KINDS}, set()
    for r in sorted(recs, key=lambda r: (-r["confidence"], r["slide_index"] if r["slide_index"] is not None else 9999, r["dedupe_key"])):
        if r["dedupe_key"] in seen:                           # the same sentence repeated verbatim (a page or a remark said twice)
            continue
        seen.add(r["dedupe_key"])
        if per[r["kind"]] < CAPS[r["kind"]]:
            per[r["kind"]] += 1
            out.append(r)
    return sorted(out, key=lambda r: (r["slide_index"] if r["slide_index"] is not None else 9999, r["start"] if r["start"] is not None else -1, r["dedupe_key"]))


def _short(t: str, n: int = 70) -> str:
    t = _WS.sub(" ", t).strip()
    return t if len(t) <= n else t[: n - 1].rsplit(" ", 1)[0] + "…"


def ensure(repo: LectureRepository, lecture_id: int, force: bool = False) -> dict:
    """Generates knowledge if missing (or `force`). Does NOT silently rewrite when evidence changed — see is_stale —
    so a student's deletions are never undone behind their back; regenerate() is the explicit action."""
    meta = repo.knowledge_meta(lecture_id)
    if meta and not force:
        # one exception to "never rewrite behind the student's back": AI study material has just become available
        # for evidence that is otherwise unchanged (the keyed sync still keeps tombstones and edits)
        if not (ai_study.load(repo, lecture_id) and meta["fingerprint"] != fingerprint(repo, lecture_id)):
            return {"generated": False}
    return regenerate(repo, lecture_id)


def regenerate(repo: LectureRepository, lecture_id: int) -> dict:
    recs = build(repo, lecture_id)
    r = repo.sync_knowledge(lecture_id, recs, fingerprint(repo, lecture_id))
    return {"generated": True, "created": r["created"], "removed": r["removed"], "total": len(r["ids"])}


def is_stale(repo: LectureRepository, lecture_id: int) -> bool:
    meta = repo.knowledge_meta(lecture_id)
    return bool(meta) and meta["fingerprint"] != fingerprint(repo, lecture_id)
