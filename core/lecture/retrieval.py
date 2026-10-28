"""Lecture-scoped hybrid retrieval over the EXISTING evidence model.

    question ─► scope parse (slide N / mm:ss) ─► follow-up condensation
             ─► BM25 (FTS5, Sprint 1)  ┐
             ─► vector similarity      ┘─► reciprocal-rank fusion ─► neighbour context ─► sufficiency verdict

* Retrieval units are `lecture_evidence` rows (transcript segments + slide text) — no parallel chunk store; every
  hit keeps its slide / timestamps / source ids and is cited by evidence id.
* Always scoped to ONE lecture id that the caller has already authorized. Every query below filters on it, and
  vectors are loaded per lecture, so nothing from another lecture/user can be ranked.
* The vector side is pluggable (`Embedder`). The default `SubwordHashEmbedder` is a real, deterministic,
  dependency-free embedding (hashed word + character n-grams): it adds typo/morphology tolerance
  ("gradiant", "backprop") on top of BM25, but it is NOT semantic — it cannot relate synonyms. A neural embedder
  (`MistralEmbedder`) is available via NOTIV_EMBEDDINGS=mistral; see its docstring.
* Every result says WHICH retrieval actually ran (`kind`): "bm25_only" (keyword), "lexical_subword" (keyword + hashed
  sub-word vectors — still NOT semantic) or "semantic" (a neural embedder answered). `semantic` is True only for the last.
  `mode` ("lexical" | "hybrid" | "scope") is the fusion mode and is kept for compatibility; "hybrid" does NOT imply semantic.
  If a neural embedder is configured but unavailable, retrieval degrades to BM25 only (never to hashed vectors compared
  against neural ones), reports `degraded: True` + a reason class, and a cool-down stops every question from re-hitting a
  failing provider.
* `sufficient` is decided from evidence, not from the retriever returning *something*: the question's own
  content terms must be covered by the top evidence, otherwise the caller must say "not enough evidence".
* Ranking is PASSAGE-level (see passages.py): an evidence row (a slide/PDF page) is cut into question blocks / paragraphs,
  scored with BM25 (the page title travels with each passage), and the cited text is the matching passage, not the whole
  page. A term that occurs in a large share of all passages ("AWS" in an AWS question bank) cannot by itself make a passage
  relevant, and a definitional question ("What is X?") is answered only from a passage that actually defines X.
"""
import difflib
import hashlib
import math
import os
import re
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .repo import LectureRepository

RRF_K = 60
BM25_CANDIDATES = 40
MIN_VECTOR_SIM = 0.22          # below this a vector match is noise
COVERAGE_REQUIRED = 0.6        # share of the question's content terms the top evidence must contain (3+ terms)
NEIGHBOUR_GAP_SECONDS = 20.0
MAX_NEIGHBOURS = 4
TIME_WINDOW = 20.0

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-]*")
STOP = frozenset("""a an the is are was were be been being am of to in on at for and or but if then so as by with from
about into over under what why how when where which who whom whose does do did done can could should would will may might
must this that these those it its i you we they he she him her his their them our your my me us not no yes also just
very more most some any each than too as there here have has had get got let tell explain give show say said says talk
talked talks mention mentioned mentions discuss discussed discusses please professor lecture lecturer teacher class
slide slides minute minutes second seconds video audio recording today thing things one use used using like
work works working help helps mean means meant example examples difference differences between relate related relationship
important key main idea concept purpose reason around near during happen happens happening going cover covers covered
overall basically actually exactly really mainly generally matter matters""".split())

_PRONOUN = re.compile(r"\b(it|its|that|this|they|them|their|those|these|he|she|his|her|there|one)\b", re.I)
_FOLLOW_LEAD = re.compile(r"^\s*(and|also|what about|how about|why|so|but|then)\b", re.I)
_SLIDE = re.compile(r"\bslides?\s*(?:number\s*|no\.?\s*|#\s*)?(\d{1,3})\b", re.I)
_CLOCK = re.compile(r"\b(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\b")
_MINUTE = re.compile(r"\bminute\s*(\d{1,3})\b", re.I)


# ── text utilities ───────────────────────────────────────────────────────
def stem(w: str) -> str:
    """Light, symmetric stemmer (applied to question AND evidence, so conflation only has to be consistent)."""
    w = w.lower().strip("'-")
    if len(w) > 4 and w.endswith("ies"):
        w = w[:-3] + "y"
    elif len(w) > 5 and w.endswith("ing"):
        w = w[:-3]
        w = w[:-1] if len(w) > 3 and w[-1] == w[-2] else w
    elif len(w) > 4 and w.endswith("ed"):
        w = w[:-2]
        w = w[:-1] if len(w) > 3 and w[-1] == w[-2] else w
    elif len(w) > 4 and w.endswith("es"):
        w = w[:-2]
    elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    if len(w) > 3 and w.endswith("e"):
        w = w[:-1]
    return w


def content_terms(text: str) -> List[str]:
    out, seen = [], set()
    for w in _WORD.findall(text or ""):
        lw = w.lower()
        if lw in STOP or len(lw) < 2:
            continue
        s = stem(lw)
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def term_covered(term: str, stems: set) -> bool:
    """Exact stem, prefix relation ("backprop"/"backpropagation") or near-miss typo."""
    if term in stems:
        return True
    if len(term) >= 5:
        for s in stems:
            if len(s) >= 5 and (s.startswith(term) or term.startswith(s)):
                return True
            if len(s) >= 5 and abs(len(s) - len(term)) <= 2 and difflib.SequenceMatcher(None, term, s).ratio() >= 0.86:
                return True
    return False


def coverage(terms: Sequence[str], texts: Sequence[str]) -> float:
    if not terms:
        return 0.0
    stems = {stem(w) for t in texts for w in _WORD.findall(t.lower())}
    return sum(term_covered(t, stems) for t in terms) / len(terms)


def required_coverage(n_terms: int) -> float:
    """Short questions must be covered completely ("quantum computing" can't ride on "computes" alone);
    longer ones may miss a minor term."""
    return 1.0 if n_terms <= 2 else COVERAGE_REQUIRED


def effective_required(n_terms: int, used_prior: bool, question: str) -> float:
    """A follow-up anchored by a pronoun ("what about ITS step size?") inherits its subject from the previous,
    already-answered turn, so only half of its own modifier terms must be found. A topic switch with no
    pronoun ("what about quantum computing?") gets the strict rule."""
    if used_prior and _PRONOUN.search(question):
        return 0.5
    return required_coverage(n_terms)


def split_sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    return [p.strip(" -•\t") for p in parts if p and p.strip(" -•\t")]


# ── embeddings ───────────────────────────────────────────────────────────
class EmbeddingUnavailable(RuntimeError):
    """A configured embedding provider cannot be used (missing key/package, timeout, API error). Never user-visible text."""


class SubwordHashEmbedder:
    """Deterministic feature-hashing embedding: stemmed word unigrams + bigrams + character 3/4-grams,
    sublinear TF, L2-normalised. Lexical/sub-word similarity only — no semantic generalisation."""
    name = "subword-hash-v1"
    dim = 1024
    neural = False

    def _feats(self, text: str):
        words = [stem(w) for w in _WORD.findall((text or "").lower()) if w.lower() not in STOP]
        for w in words:
            yield "w:" + w, 1.0
            if len(w) >= 4:
                p = f"^{w}$"
                for n in (3, 4):
                    for i in range(len(p) - n + 1):
                        yield "c:" + p[i:i + n], 0.35
        for a, b in zip(words, words[1:]):
            yield f"b:{a}_{b}", 0.5

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for r, t in enumerate(texts):
            counts: Dict[Tuple[int, int], float] = {}
            for f, wgt in self._feats(t):
                h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "little")
                key = (h % self.dim, 1 if (h >> 63) & 1 else -1)
                counts[key] = counts.get(key, 0.0) + wgt
            for (i, sgn), c in counts.items():
                out[r, i] += sgn * (1.0 + math.log(c) if c >= 1 else c)
            n = np.linalg.norm(out[r])
            if n:
                out[r] /= n
        return out


class MistralEmbedder:
    """Neural embeddings via langchain_mistralai (same provider family as the rest of Notiv).
    Selected with NOTIV_EMBEDDINGS=mistral + MISTRAL_API_KEY. Calls have a timeout (NOTIV_EMBED_TIMEOUT_SECONDS, default 15)
    and one retry. NOT exercised by the offline test-suite against the real service (UNVERIFIED live): the failure/fallback
    behaviour around it is tested with real code and a failing stand-in provider."""
    name = "mistral-embed"
    dim = 1024
    neural = True

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        key = os.getenv("MISTRAL_API_KEY")
        if not key:
            raise EmbeddingUnavailable("MISTRAL_API_KEY is not set")
        try:
            from langchain_mistralai import MistralAIEmbeddings
        except ImportError as e:
            raise EmbeddingUnavailable("langchain_mistralai is not installed") from e
        try:
            emb = MistralAIEmbeddings(model="mistral-embed", mistral_api_key=key, timeout=_embed_timeout(), max_retries=1)
            v = np.asarray(emb.embed_documents(list(texts)), dtype=np.float32)
        except Exception as e:                           # noqa: BLE001 — provider/network errors, never leak their text
            raise EmbeddingUnavailable(type(e).__name__) from e
        if v.ndim != 2 or v.shape[1] != self.dim:
            raise EmbeddingUnavailable("unexpected embedding shape")
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)


def _embed_timeout() -> float:
    try:
        return max(1.0, float(os.getenv("NOTIV_EMBED_TIMEOUT_SECONDS", "15")))
    except ValueError:
        return 15.0


EMBEDDING_CHOICES = ("local", "mistral")


def embedding_config() -> dict:
    """Validates NOTIV_EMBEDDINGS / provider settings WITHOUT calling any provider.
    {"configured": str, "effective": "local"|"mistral", "neural": bool, "problems": [str]}. A broken choice resolves to the
    local (non-semantic) embedder so Ask keeps working, and is reported — never silently treated as semantic."""
    want = (os.getenv("NOTIV_EMBEDDINGS", "local") or "local").strip().lower()
    problems = []
    if want not in EMBEDDING_CHOICES:
        problems.append(f"NOTIV_EMBEDDINGS must be one of {', '.join(EMBEDDING_CHOICES)} (got an unknown value)")
        return {"configured": want, "effective": "local", "neural": False, "problems": problems}
    if want == "mistral":
        if not os.getenv("MISTRAL_API_KEY"):
            problems.append("NOTIV_EMBEDDINGS=mistral requires MISTRAL_API_KEY")
        import importlib.util
        if importlib.util.find_spec("langchain_mistralai") is None:
            problems.append("NOTIV_EMBEDDINGS=mistral requires the langchain-mistralai package")
        if problems:
            return {"configured": want, "effective": "local", "neural": False, "problems": problems}
    return {"configured": want, "effective": want, "neural": want == "mistral", "problems": []}


def default_embedder():
    return MistralEmbedder() if embedding_config()["effective"] == "mistral" else SubwordHashEmbedder()


# cool-down so a failing neural provider is not retried (and waited on) for every single question
_breaker = {"until": 0.0, "last_reason": ""}


def _cooldown() -> float:
    try:
        return max(0.0, float(os.getenv("NOTIV_EMBED_COOLDOWN_SECONDS", "30")))
    except ValueError:
        return 30.0


def reset_breaker() -> None:
    _breaker.update(until=0.0, last_reason="")


def retrieval_status() -> dict:
    """Operator-facing: what retrieval is configured vs actually in effect."""
    import time as _t
    cfg = embedding_config()
    cooling = _breaker["until"] > _t.time()
    kind = "semantic" if cfg["neural"] and not cooling else "lexical_subword"
    return {**cfg, "kind": kind, "degraded": bool(cfg["problems"]) and cfg["configured"] == "mistral" or cooling,
            "cooling_down": cooling, "last_failure": _breaker["last_reason"] or None}


def _fingerprint(evidence: List[dict], model: str) -> str:
    h = hashlib.sha1(model.encode())
    for e in evidence:
        h.update(repr((e["id"], e["text"])).encode())
    return h.hexdigest()


def ensure_embeddings(repo: LectureRepository, lecture_id: int, embedder=None) -> bool:
    """(Re)computes this lecture's evidence vectors if missing/stale. True if it wrote."""
    emb = embedder or default_embedder()
    ev = repo.list_evidence(lecture_id)
    fp = _fingerprint(ev, emb.name)
    meta = repo.embedding_meta(lecture_id)
    if meta and meta["fingerprint"] == fp:
        return False
    vecs = emb.embed([e["text"] for e in ev]) if ev else np.zeros((0, emb.dim), np.float32)
    repo.replace_embeddings(lecture_id, emb.name, int(vecs.shape[1]) if len(ev) else emb.dim,
                            [(e["id"], np.asarray(v, np.float32).tobytes()) for e, v in zip(ev, vecs)], fp)
    return True


# ── question understanding ───────────────────────────────────────────────
def parse_scope(question: str) -> Tuple[str, dict]:
    """Pulls "slide 3" / "at 12:30" / "minute 5" out of the question. Returns (question without them, scope)."""
    scope: dict = {}
    q = question
    m = _SLIDE.search(q)
    if m:
        scope["slide"] = int(m.group(1))
        q = q.replace(m.group(0), " ")
    m = _CLOCK.search(q)
    if m:
        h, mi, s = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
        if s < 60 and (m.group(1) or mi < 60):
            t = h * 3600 + mi * 60 + s
            scope["time"] = (max(0.0, t - TIME_WINDOW), t + TIME_WINDOW)
            q = q.replace(m.group(0), " ")
    else:
        m = _MINUTE.search(q)
        if m:
            t = int(m.group(1)) * 60
            scope["time"] = (float(t), float(t + 60))
            q = q.replace(m.group(0), " ")
    return q, scope


def condense(question_terms: List[str], question: str, prior_question: Optional[str]) -> Tuple[List[str], bool]:
    """Follow-up handling: when the question leans on the previous turn ("what about its step size?", "why?"),
    the RETRIEVAL query also gets the previous question's terms. Sufficiency is still judged on the
    question's own terms, so an unrelated follow-up can't borrow support from the old topic."""
    if not prior_question:
        return question_terms, False
    own = len(question_terms)
    leans = own <= 1 or (own <= 3 and (_PRONOUN.search(question) or _FOLLOW_LEAD.match(question)))
    if not leans:
        return question_terms, False
    prior = [t for t in content_terms(parse_scope(prior_question)[0]) if t not in question_terms]
    return question_terms + prior, bool(prior)


# ── retrieval ────────────────────────────────────────────────────────────
def _in_scope(e: dict, scope: dict) -> bool:
    if "slide" in scope and e["slide_index"] != scope["slide"]:
        return False
    if "time" in scope:
        lo, hi = scope["time"]
        if e["start"] is None or not (lo <= e["start"] <= hi or (e["end"] is not None and e["start"] <= lo <= e["end"])):
            return False
    return True


def _log_degraded(reason) -> None:
    try:
        from . import obs
        obs.inc("notiv_retrieval_degraded_total", help="questions answered without vector retrieval", reason=str(reason)[:40])
        obs.log_event(obs.get_logger("retrieval"), "retrieval_degraded", level=30, reason=str(reason)[:80])
    except Exception:                                   # noqa: BLE001 — observability must never break retrieval
        pass


def retrieve(repo: LectureRepository, lecture_id: int, question: str, prior_question: Optional[str] = None,
             embedder=None, k: int = 8) -> dict:
    """Returns {chunks, sufficient, reason, coverage, mode, scope, terms}. `chunks` are evidence rows
    (+ `scores`, `context`). `lecture_id` MUST already be authorized by the caller."""
    cleaned, scope = parse_scope(question)
    own_terms = content_terms(cleaned)
    query_terms, used_prior = condense(own_terms, cleaned, prior_question)
    all_ev = repo.list_evidence(lecture_id)
    by_id = {e["id"]: e for e in all_ev}
    in_scope = {i: e for i, e in by_id.items() if _in_scope(e, scope)}
    base = {"check_terms": [], "scope": {k_: (list(v) if isinstance(v, tuple) else v) for k_, v in scope.items()}, "terms": own_terms,
            "used_prior_context": used_prior, "mode": "lexical", "coverage": 0.0,
            "kind": "bm25_only", "semantic": False, "embedder": None, "degraded": False, "degraded_reason": None}

    if scope and not in_scope:
        return {**base, "chunks": [], "sufficient": False, "reason": "out_of_scope"}

    # scope-only question ("what is on slide 3?"): everything in scope, slide text first, then speech in time order
    if not query_terms:
        if not scope:
            return {**base, "chunks": [], "sufficient": False, "reason": "no_terms"}
        rows = sorted(in_scope.values(), key=lambda e: (e["source_type"] != "slide", e["start"] if e["start"] is not None else 0, e["id"]))[:k]
        return {**base, "mode": "scope", "kind": "scope", "coverage": 1.0, "sufficient": bool(rows), "reason": None if rows else "no_match",
                "chunks": [dict(e, scores={"rrf": None, "bm25_rank": None, "vector_sim": None}, context=False) for e in rows]}

    query = " ".join(query_terms)
    from . import passages as P
    pidx = P.get_index(lecture_id, all_ev, P.fingerprint(all_ev))
    intent = P.analyze_question(cleaned)
    ranks: Dict[int, dict] = {}
    best_units: Dict[int, List[int]] = {}                 # evidence id -> passage (unit) indexes to quote, best first
    phrase = intent.subject_terms if intent.kind == "definition" else (own_terms if 2 <= len(own_terms) <= 4 else None)
    n_ranked = 0
    for ui, _score in pidx.search(query_terms, phrase=phrase, limit=BM25_CANDIDATES * 6):
        u = pidx.units[ui]
        if u.eid not in in_scope:
            continue
        if "bm25_rank" not in ranks.get(u.eid, {}):
            n_ranked += 1
            ranks.setdefault(u.eid, {})["bm25_rank"] = n_ranked
        if len(best_units.setdefault(u.eid, [])) < 2:
            best_units[u.eid].append(ui)

    # a passage that actually DEFINES the subject outranks everything that merely mentions it
    definition_ids: List[int] = []
    unit_pos = [i for i, u in enumerate(pidx.units) if u.eid in in_scope]
    if intent.kind == "definition":
        scoped_units = [pidx.units[i] for i in unit_pos]
        for j, sent, how in P.find_definitions(scoped_units, intent.subject)[:5]:
            u = scoped_units[j]
            ui = unit_pos[j]
            ranks.setdefault(u.eid, {})["definition"] = how
            best_units[u.eid] = [ui] + [x for x in best_units.get(u.eid, []) if x != ui][:1]
            if u.eid not in definition_ids:
                definition_ids.append(u.eid)

    mode, kind, semantic, degraded, reason = "lexical", "bm25_only", False, False, None
    emb = None
    try:
        emb = embedder or default_embedder()
        neural = bool(getattr(emb, "neural", False))
        import time as _t
        if neural and _breaker["until"] > _t.time():            # provider failed recently: do not wait on it again yet
            raise EmbeddingUnavailable("cooling down after: " + (_breaker["last_reason"] or "failure"))
        ensure_embeddings(repo, lecture_id, emb)
        rows = [(eid, np.frombuffer(b, dtype=np.float32)) for eid, b in repo.load_embeddings(lecture_id)]
        rows = [(eid, v) for eid, v in rows if eid in in_scope and v.shape[0] == emb.dim]
        if rows:
            q = emb.embed([query])[0]
            sims = sorted(((float(v @ q), eid) for eid, v in rows), reverse=True)
            r = 0
            for sim, eid in sims:
                if sim < MIN_VECTOR_SIM:
                    break
                r += 1
                ranks.setdefault(eid, {}).update(vector_rank=r, vector_sim=round(sim, 4))
            mode = "hybrid"
            semantic = neural
            kind = "semantic" if neural else "lexical_subword"
    except Exception as e:                              # noqa: BLE001 — embedder down -> BM25 retrieval still works
        mode, kind, semantic, degraded = "lexical", "bm25_only", False, True
        reason = type(e).__name__ if not isinstance(e, EmbeddingUnavailable) else str(e)[:80]
        for eid in ranks:                               # drop any half-added vector ranks (consistency: BM25 only)
            ranks[eid].pop("vector_rank", None); ranks[eid].pop("vector_sim", None)
        if getattr(emb, "neural", False) and not str(reason).startswith("cooling down"):
            import time as _t
            _breaker.update(until=_t.time() + _cooldown(), last_reason=reason)
        _log_degraded(reason)
    # a configured-but-unusable provider (bad config) is also a degradation, even though the local embedder answered
    cfg_problems = embedding_config()["problems"] if embedder is None else []
    if cfg_problems and not degraded:
        degraded, reason = True, "provider_not_configured"

    for eid, r in ranks.items():
        r["rrf"] = round(sum(1.0 / (RRF_K + r[x]) for x in ("bm25_rank", "vector_rank") if x in r) + (1.0 if "definition" in r else 0.0), 6)
    ordered = sorted(ranks, key=lambda i: (-ranks[i]["rrf"], by_id[i]["start"] if by_id[i]["start"] is not None else 0, i))[:k]

    units_of: Dict[int, List[int]] = {}
    for i, u in enumerate(pidx.units):
        units_of.setdefault(u.eid, []).append(i)

    def quote(eid: int) -> str:
        """The text of the passage(s) that matched, in page order; the whole evidence text when it is a single passage."""
        sel = sorted(set(best_units.get(eid, [])), key=lambda i: pidx.units[i].k)
        if not sel or len(sel) >= len(units_of.get(eid, [])):
            return by_id[eid]["text"]
        return "\n".join(pidx.units[i].text for i in sel)

    top = [dict(by_id[i], text=quote(i), scores={"rrf": ranks[i]["rrf"], "bm25_rank": ranks[i].get("bm25_rank"),
                                                   "vector_sim": ranks[i].get("vector_sim")},
                context=False, definition=ranks[i].get("definition")) for i in ordered]

    # neighbour context: the lines right before/after the best speech hits (real evidence, shown + citable)
    speech = sorted((e for e in in_scope.values() if e["source_type"] == "transcript" and e["start"] is not None),
                    key=lambda e: e["start"])
    pos = {e["id"]: n for n, e in enumerate(speech)}
    have = {c["id"] for c in top}
    extra = []
    for c in [c for c in top if c["source_type"] == "transcript"][:3]:
        n = pos.get(c["id"])
        for m in (n - 1, n + 1) if n is not None else ():
            if 0 <= m < len(speech) and speech[m]["id"] not in have and len(extra) < MAX_NEIGHBOURS:
                gap = abs(speech[m]["start"] - c["start"])
                if gap <= NEIGHBOUR_GAP_SECONDS + (c["end"] - c["start"] if c["end"] else 0):
                    have.add(speech[m]["id"])
                    extra.append(dict(speech[m], scores={"rrf": None, "bm25_rank": None, "vector_sim": None}, context=True))

    check_terms = own_terms or query_terms
    base["check_terms"] = check_terms
    cov = coverage(check_terms, [c["text"] for c in top[:3]])
    required = effective_required(len(check_terms), used_prior, cleaned)
    sufficient = bool(top) and cov >= required - 1e-9
    why = None if sufficient else ("no_match" if not top else "weak_match")

    # ── what do the passages say about the subject? (drives the honest "the material doesn't define X" answer) ──
    info = {"intent": intent.kind, "subject": intent.subject or None, "definition_found": bool(definition_ids),
            "mentions": 0, "mention_ids": [], "question_like_ratio": 0.0, "ubiquity": 0.0, "broad": False}
    subj_terms = intent.subject_terms if intent.kind == "definition" else check_terms
    if subj_terms and pidx.n:
        ubi = [pidx.df(t) / pidx.n for t in subj_terms if t in pidx.postings]
        info["ubiquity"] = round(max(ubi), 3) if ubi else 0.0
        info["broad"] = all(pidx.ubiquitous(t) for t in subj_terms)
        mention = [u for u in pidx.units if u.eid in in_scope and all(t in set(u.tokens) for t in subj_terms)]
        info["mentions"] = len(mention)
        info["mention_ids"] = list(dict.fromkeys(u.eid for u in mention))[:3]
        info["question_like_ratio"] = round(sum(P.is_question_like(u.text) for u in mention) / len(mention), 2) if mention else 0.0
    if definition_ids:
        sufficient, why = True, None
        if all(t in set(pidx.units[best_units[definition_ids[0]][0]].tokens) for t in intent.subject_terms):
            cov = 1.0
    elif intent.kind == "definition" and info["broad"]:
        sufficient, why = False, "no_definition"          # "AWS" is on nearly every passage but no passage says what it IS
    elif info["broad"] and not scope and len(check_terms) <= 3 and n_ranked >= 3:
        sufficient, why = False, "broad_topic"            # every term is everywhere: lexical overlap proves nothing
    return {**base, "required": required, "mode": mode, "kind": kind, "semantic": semantic, "embedder": getattr(emb, "name", None),
            "degraded": degraded, "degraded_reason": reason, "coverage": round(cov, 3),
            "chunks": top + extra, "sufficient": sufficient, "reason": why, "subject_info": info}
