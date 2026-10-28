"""Passage-level retrieval helpers for Ask Notiv.

Why this exists. Evidence is stored one row per slide/PDF page. A question-bank page can hold ten unrelated questions,
so ranking whole pages makes any word that appears on most pages ("AWS") match everything, and quoting the page back
dumps unrelated questions on the reader. This module works BELOW the evidence row, in memory, without changing the
evidence model, the schema or any citation:

  * split_passages()   cuts a page into question blocks / paragraphs / sentence groups (a transcript line is one passage);
  * PassageIndex       Okapi BM25 over passages (the slide title is indexed with every passage of its page) with
                       corpus statistics: inverse document frequency and "ubiquitous" terms;
  * analyze_question() recognises definitional questions ("What is AWS?", "define X", "what does X stand for") and
                       extracts the subject;
  * find_definitions() finds passages that actually DEFINE the subject (X is/are/means/refers to…, "X: …", a page titled
                       "What is X"), excluding questions that merely contain it.

Every passage keeps its parent evidence id, so every citation still points at a real evidence row (slide / timestamp).
"""
import hashlib
import math
import re
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .retrieval import STOP, _WORD, content_terms, stem

MAX_PASSAGE_CHARS = 600
SPLIT_ABOVE_CHARS = 420            # evidence at or below this stays a single passage
MIN_PASSAGE_CHARS = 45
UBIQUITOUS_RATIO = 0.35            # a term in at least this share of passages cannot, alone, show that a passage is "about" it
UBIQUITY_MIN_PASSAGES = 12         # statistics from a tiny corpus mean nothing

_ITEM_START = re.compile(r"^\s*(?:q(?:uestion)?\s*)?\d{1,3}\s*[.):]\s+\S", re.I)
_BLANK = re.compile(r"\n\s*\n+")
_SENT_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9“\"'(])")


@dataclass
class Unit:
    eid: int                   # parent evidence id (what gets cited)
    k: int                     # passage number within the evidence row
    text: str                  # what is shown / quoted
    heading: Optional[str]     # first line of the page when it looks like a title
    tokens: List[str] = field(default_factory=list)       # stems of heading + text (indexed)
    source_type: str = ""


# ── splitting ────────────────────────────────────────────────────────────
def _is_heading(line: str) -> bool:
    w = line.split()
    return 0 < len(w) <= 10 and not line.rstrip().endswith((".", "?", "!", ";", ",")) and len(line) <= 90


def _hard_split(block: str) -> List[str]:
    if len(block) <= MAX_PASSAGE_CHARS:
        return [block]
    out, cur = [], ""
    for s in _SENT_END.split(block):
        if cur and len(cur) + len(s) + 1 > MAX_PASSAGE_CHARS:
            out.append(cur)
            cur = s
        else:
            cur = (cur + " " + s).strip()
    if cur:
        out.append(cur)
    final = []
    for p in out:                                    # a single enormous sentence: cut on whitespace
        while len(p) > MAX_PASSAGE_CHARS * 1.5:
            cut = p.rfind(" ", 0, MAX_PASSAGE_CHARS)
            cut = cut if cut > 100 else MAX_PASSAGE_CHARS
            final.append(p[:cut].strip())
            p = p[cut:].strip()
        final.append(p)
    return [p for p in final if p]


def split_passages(text: str) -> Tuple[Optional[str], List[str]]:
    """(heading, passages). A short text is ONE passage. Longer text is cut at numbered items (question banks), then blank
    lines, then sentence groups; fragments shorter than MIN_PASSAGE_CHARS are merged into their neighbour."""
    text = (text or "").replace("\r", "").strip()
    if not text:
        return None, []
    lines = [ln.strip() for ln in text.split("\n")]
    heading = lines[0] if len(lines) > 1 and _is_heading(lines[0]) else None
    if len(text) <= SPLIT_ABOVE_CHARS:
        return heading, [text]
    body_lines = lines[1:] if heading else lines
    # 1) numbered items ("12. Which service …?" + its options) are the natural unit of a question bank
    blocks: List[str] = []
    if sum(1 for ln in body_lines if _ITEM_START.match(ln)) >= 2:
        cur: List[str] = []
        for ln in body_lines:
            if _ITEM_START.match(ln) and cur:
                blocks.append("\n".join(cur))
                cur = []
            if ln:
                cur.append(ln)
        if cur:
            blocks.append("\n".join(cur))
    else:                                                      # 2) paragraphs
        body = "\n".join(body_lines)
        blocks = [b.strip() for b in _BLANK.split(body) if b.strip()]
        if len(blocks) == 1:                                   # 3) one wall of text: bullet lines, else sentences
            bullets = [ln for ln in body_lines if ln]
            blocks = bullets if len(bullets) >= 4 else [body]
    merged: List[str] = []
    for b in blocks:
        b = " ".join(b.split()) if "\n" not in b else b
        if merged and (len(merged[-1]) < MIN_PASSAGE_CHARS or len(b) < MIN_PASSAGE_CHARS) and len(merged[-1]) + len(b) < MAX_PASSAGE_CHARS:
            merged[-1] = merged[-1] + "\n" + b
        else:
            merged.append(b)
    out: List[str] = []
    for b in merged:
        out.extend(_hard_split(b))
    if heading and out:
        out[0] = heading + "\n" + out[0]                       # the title stays with the first passage so quotes keep their context
    return heading, out or [text]


# ── index ────────────────────────────────────────────────────────────────
def _tokens(text: str) -> List[str]:
    return [stem(w) for w in _WORD.findall((text or "").lower()) if w.lower() not in STOP and len(w) >= 2]


class PassageIndex:
    K1, B = 1.2, 0.75

    def __init__(self, evidence: Sequence[dict]):
        self.units: List[Unit] = []
        for e in evidence:
            heading, parts = split_passages(e["text"])
            for k, p in enumerate(parts):
                u = Unit(e["id"], k, p, heading, source_type=e["source_type"])
                u.tokens = _tokens(((heading + " ") if heading and not p.startswith(heading) else "") + p)
                self.units.append(u)
        self.postings: Dict[str, List[Tuple[int, int]]] = {}
        for i, u in enumerate(self.units):
            for t, c in Counter(u.tokens).items():
                self.postings.setdefault(t, []).append((i, c))
        n = len(self.units)
        self.n = n
        self.avgdl = (sum(len(u.tokens) for u in self.units) / n) if n else 1.0
        self._vocab_by_first: Dict[str, List[str]] = {}
        for t in self.postings:
            self._vocab_by_first.setdefault(t[:1], []).append(t)

    def df(self, term: str) -> int:
        return len(self.postings.get(term, ()))

    def idf(self, term: str) -> float:
        d = self.df(term)
        return math.log(1.0 + (self.n - d + 0.5) / (d + 0.5))

    def ubiquitous(self, term: str) -> bool:
        return self.n >= UBIQUITY_MIN_PASSAGES and self.df(term) / self.n >= UBIQUITOUS_RATIO

    def _resolve(self, term: str) -> List[str]:
        """Query term -> indexed terms: itself, else prefix relatives / near-miss typos (only when it is not indexed)."""
        if term in self.postings:
            return [term]
        if len(term) < 4:
            return []
        out = []
        import difflib
        for v in self._vocab_by_first.get(term[:1], ()):
            if abs(len(v) - len(term)) > 2 and not (len(v) >= 5 and len(term) >= 5 and (v.startswith(term) or term.startswith(v))):
                continue
            if (len(v) >= 5 and len(term) >= 5 and (v.startswith(term) or term.startswith(v))) or \
                    difflib.SequenceMatcher(None, term, v).ratio() >= 0.86:
                out.append(v)
        return out[:4]

    def search(self, terms: Sequence[str], phrase: Optional[Sequence[str]] = None, limit: int = 40) -> List[Tuple[int, float]]:
        """BM25 over passages -> [(unit_index, score)] best first. `phrase` (stems, in order) earns a bonus when it occurs
        consecutively in a passage."""
        scores: Dict[int, float] = {}
        for t in dict.fromkeys(terms):
            for v in self._resolve(t):
                idf = self.idf(v)
                weight = 1.0 if v == t else 0.6
                for i, tf in self.postings[v]:
                    dl = len(self.units[i].tokens)
                    s = idf * (tf * (self.K1 + 1)) / (tf + self.K1 * (1 - self.B + self.B * dl / self.avgdl))
                    scores[i] = scores.get(i, 0.0) + weight * s
        if phrase and len(phrase) >= 2:
            ph = list(phrase)
            bonus = sum(self.idf(t) for t in ph) * 0.5
            for i in list(scores):
                tk = self.units[i].tokens
                if any(tk[j:j + len(ph)] == ph for j in range(len(tk) - len(ph) + 1)):
                    scores[i] += bonus
        return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]


_CACHE: "OrderedDict[str, PassageIndex]" = OrderedDict()
_CACHE_MAX = 16


def get_index(lecture_id: int, evidence: Sequence[dict], fingerprint: str) -> PassageIndex:
    key = f"{lecture_id}:{fingerprint}"
    idx = _CACHE.get(key)
    if idx is not None:
        _CACHE.move_to_end(key)
        return idx
    idx = PassageIndex(evidence)
    _CACHE[key] = idx
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return idx


def clear_cache() -> None:
    _CACHE.clear()


def fingerprint(evidence: Iterable[dict]) -> str:
    h = hashlib.sha1(b"passages-v1")
    for e in evidence:
        h.update(repr((e["id"], e["text"])).encode())
    return h.hexdigest()


# ── question understanding ───────────────────────────────────────────────
@dataclass
class Intent:
    kind: str = "other"                  # definition | compare | other
    subject: str = ""                    # surface text of the subject, as asked ("AWS")
    subject_terms: List[str] = field(default_factory=list)       # stems


_DEF_LEAD = re.compile(
    r"^\s*(?:(?:so|ok|okay|and|also)[,\s]+)?(?:what(?:'s|\s+is|\s+are|\s+was|\s+were)|who(?:'s|\s+is|\s+are)|define|definition\s+of|"
    r"meaning\s+of|explain|describe|tell\s+me\s+about|give\s+me\s+an?\s+(?:overview|introduction|summary)\s+of|overview\s+of|"
    r"introduction\s+to|what\s+do\s+you\s+mean\s+by|can\s+you\s+explain|please\s+explain)\s+(?P<s>.+?)\s*[?.!]*\s*$", re.I)
_DEF_STAND = re.compile(r"^\s*what\s+does\s+(?P<s>.+?)\s+(?:stand\s+for|mean)\s*[?.!]*\s*$", re.I)
_COMPARE = re.compile(r"\b(?:vs\.?|versus|difference|differences|differ|compare|compared|comparison|between|better|worse)\b", re.I)
_ARTICLE = re.compile(r"^(?:an?|the|this|that|these|those|about|exactly)\s+", re.I)
_SUBJ_CUT = re.compile(r"\s+(?:and|but|or|so|because|in|for|of|used|used\s+for|works?|do|does|did)\s+(?:why|how|what|when|where|it|they|this)\b|[,;]|\s+&\s+", re.I)


def analyze_question(question: str) -> Intent:
    q = " ".join((question or "").split())
    if _COMPARE.search(q):
        return Intent("compare")
    m = _DEF_STAND.match(q) or _DEF_LEAD.match(q)
    if not m:
        return Intent()
    subj = m.group("s").strip()
    subj = _SUBJ_CUT.split(subj)[0].strip()
    while True:
        s2 = _ARTICLE.sub("", subj)
        if s2 == subj:
            break
        subj = s2
    terms = content_terms(subj)
    if not terms or len(subj.split()) > 6:
        return Intent()
    return Intent("definition", subj, terms)


_DEF_VERB = (r"(?:is|are|was|were|means|refers?\s+to|stands?\s+for|denotes|represents|describes|"
             r"can\s+be\s+defined\s+as|is\s+defined\s+as|is\s+known\s+as|are\s+known\s+as|is\s+called)")
_TITLE_PREFIX = re.compile(r"^(?:what\s+(?:is|are)\s+|introduction\s+to\s+|intro\s+to\s+|overview\s+of\s+|about\s+|understanding\s+|"
                           r"defining\s+|definition\s+of\s+)?(?:an?\s+|the\s+)?", re.I)


def _subject_regex(subject: str) -> "re.Pattern":
    words = [re.escape(w) for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", subject)]
    return re.compile(r"(?<![\w])" + r"[\s\-_/]+".join(words) + r"(?![\w])", re.I)


def find_definitions(units: Sequence[Unit], subject: str) -> List[Tuple[int, str, str]]:
    """Passages that DEFINE `subject`: [(unit_index, defining_sentence, how)] best first.
    how = "statement" (X is/means/refers to …), "label" (X: …), "title" (the page is titled "What is X" / "X")."""
    if not subject.strip():
        return []
    subj = _subject_regex(subject)
    stmt = re.compile(subj.pattern + r"(?:\s*\([^)]{0,80}\))?\s*(?:,\s*[^,.?!]{1,60},)?\s+" + _DEF_VERB + r"\b", re.I)
    label = re.compile(r"^\s*[-•*]?\s*" + subj.pattern + r"\s*[:–—-]\s+\S", re.I)
    found: List[Tuple[int, str, str, int]] = []
    for i, u in enumerate(units):
        body = u.text
        title_hit = bool(u.heading and _TITLE_PREFIX.sub("", u.heading.strip().rstrip("?:.")).strip().lower() ==
                         " ".join(re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", subject)).lower())
        if title_hit and u.k == 0:
            rest = body[len(u.heading):].strip() if body.startswith(u.heading) else body
            if len(rest) >= 40:
                found.append((i, " ".join(rest.split())[:300], "title", 2))
                continue
        for sent in re.split(r"(?<=[.!?])\s+|\n+", body):
            s = sent.strip(" -•*\t")
            if not s or s.endswith("?"):
                continue
            if stmt.search(s):
                found.append((i, s, "statement", 3 if s.lower().lstrip().startswith(subject.lower()) else 2))
                break
            if label.match(sent):
                found.append((i, s, "label", 1))
                break
    found.sort(key=lambda t: (-t[3], t[0]))
    return [(i, s, how) for i, s, how, _ in found]


def is_question_like(text: str) -> bool:
    t = text or ""
    return "?" in t or bool(_ITEM_START.match(t.strip().split("\n")[0] if t.strip() else ""))
