"""Grounded Q&A over lecture evidence (lecture- or course-scoped).

    question -> BM25 retrieval over evidence -> LLM -> citation verification

Guarantees (enforced in code, not by prompt alone):
  * no retrieved evidence  -> fixed NOT_ENOUGH message, LLM never called
  * every [E<id>] the model cites must be an evidence id that was actually
    shown to it; unknown ids are removed and reported in `dropped_citations`
  * an answer left with zero valid citations is replaced by NOT_ENOUGH
  * returned citations carry the evidence's real slide/timestamps/text
Retrieval is lexical (FTS5 BM25); semantic/hybrid retrieval is a later step.
"""
import re
from typing import Callable, List, Optional

from .repo import LectureRepository

NOT_ENOUGH = "Not enough evidence was found in this lecture to answer confidently."
MAX_EVIDENCE = 8
MAX_CONTEXT_CHARS = 6000
MAX_HISTORY_TURNS = 3

SYSTEM = f"""You are a study assistant answering ONLY from the lecture evidence below.
Each evidence item starts with an id like [E12]. After every claim, cite the id(s) that support it, e.g. [E12][E15].
Only cite ids that appear below. Never invent or reformat ids, slide numbers or timestamps.
If the evidence does not answer the question, reply exactly: {NOT_ENOUGH}
If evidence conflicts, say so explicitly. Be concise.
After the answer, optionally add a line "Follow-ups:" then up to 3 short follow-up questions answerable from this lecture, one per line starting with "- "."""

_CITE = re.compile(r"\[E(\d+)\]")


def _mmss(t) -> str:
    t = max(0, int(t))
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _label(ev: dict) -> str:
    bits = []
    if ev["slide_index"] is not None:
        bits.append(f"Slide {ev['slide_index']}")
    if ev["start"] is not None:
        bits.append(_mmss(ev["start"]))
    return ", ".join(bits) or ev["kind"]


def _build_context(evidence: List[dict]):
    parts, used, total = [], [], 0
    for ev in evidence:
        line = f"[E{ev['evidence_id']}] ({ev['kind']}; {_label(ev)}) {ev['text']}"
        if used and total + len(line) > MAX_CONTEXT_CHARS:
            break
        parts.append(line)
        used.append(ev)
        total += len(line)
    return "\n".join(parts), used


def default_llm(system: str, user: str) -> str:
    """The configured provider chain (OpenAI / Mistral / Anthropic with fallback, see llm.py). Imported lazily so the
    module works (and tests run) without any provider SDK."""
    from . import config
    return config.get_llm_chain()(system, user)


def _split_followups(text: str):
    m = re.search(r"^\s*Follow-ups?:\s*$", text, flags=re.I | re.M)
    if not m:
        return text.strip(), []
    body, tail = text[:m.start()].strip(), text[m.end():]
    fu = [l.strip()[2:].strip() for l in tail.splitlines() if l.strip().startswith("- ")]
    return body, [f for f in fu if f][:3]


def ask(repo: LectureRepository, lecture_ids: List[int], question: str, history: Optional[list] = None,
        llm: Optional[Callable[[str, str], str]] = None) -> dict:
    """`lecture_ids` must already be authorized (authz.accessible_lecture_ids). Returns {answer, grounded, citations[], follow_ups[], dropped_citations[]}.
    `llm(system, user) -> str` defaults to Mistral; injectable for tests/other providers."""
    evidence = repo.search_evidence(lecture_ids, question, limit=MAX_EVIDENCE, match="any")
    if not evidence:
        return {"answer": NOT_ENOUGH, "grounded": False, "citations": [], "follow_ups": [], "dropped_citations": []}

    context, used = _build_context(evidence)
    hist = ""
    if history:
        hist = "Recent conversation:\n" + "\n".join(
            f"Q: {t.get('question','')}\nA: {t.get('answer','')}" for t in list(history)[-MAX_HISTORY_TURNS:]) + "\n\n"
    raw = (llm or default_llm)(SYSTEM, f"{hist}Evidence:\n{context}\n\nQuestion: {question}")

    shown = {e["evidence_id"]: e for e in used}
    cited, dropped = [], []
    for m in _CITE.finditer(raw):
        eid = int(m.group(1))
        if eid in shown:
            if eid not in cited:
                cited.append(eid)
        elif eid not in dropped:
            dropped.append(eid)

    body, follow_ups = _split_followups(_CITE.sub(lambda m: m.group(0) if int(m.group(1)) in shown else "", raw))
    if not cited or body.strip().startswith(NOT_ENOUGH):
        return {"answer": NOT_ENOUGH, "grounded": False, "citations": [], "follow_ups": [],
                "dropped_citations": dropped}
    return {"answer": body, "grounded": True, "follow_ups": follow_ups, "dropped_citations": dropped,
            "citations": [{"evidence_id": i, "lecture_id": shown[i]["lecture_id"], "kind": shown[i]["kind"],
                           "slide_index": shown[i]["slide_index"], "start": shown[i]["start"],
                           "end": shown[i]["end"], "text": shown[i]["text"]} for i in cited]}
