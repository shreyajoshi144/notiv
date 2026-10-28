"""Study intelligence service: knowledge review, study items (flashcards / short answer / MCQ), study sessions,
grading and progress. Built on knowledge.py (verbatim, evidence-anchored items) and the Sprint 1-3 stack
(authz, evidence presentation via WorkspaceService).

Never invents content:
  * flashcard / short-answer answers are the lecture's own sentence or line, verbatim
  * cloze blanks are cut out of a verbatim sentence
  * MCQ distractors are the lecture's OTHER definitions / terms (real lecture text, wrong for this prompt)
  * every item stores the evidence links + quotes it relies on; the store re-verifies them on every write
  * when the lecture lacks the evidence for a type, that type is skipped and the response says why
Answers (and the evidence reveal) are withheld from session payloads until the learner has answered.
User edits mark an item `edited` (no longer claimed as machine-verified, never overwritten by regeneration).
"""
import hashlib
import random
import re
from typing import Dict, List, Optional, Sequence, Tuple

from . import ai_study, authz, knowledge, retrieval
from .errors import ConflictError, NotFoundError, ValidationError
from .provenance import mmss
from .repo import LectureRepository
from .storage import StorageService
from .workspace import WorkspaceService

KINDS = ("flashcard", "short_answer", "mcq")
DIFFICULTIES = ("easy", "medium", "hard")
GRADES = ("know", "unsure", "dont_know")
MASTERY_STREAK = 2
MAX_SESSION_ITEMS = 50
DEFAULT_SESSION_ITEMS = 10
MAX_PROMPT, MAX_ANSWER, MAX_RESPONSE = 500, 2000, 2000
OPTION_LETTERS = "abcdef"

_NUM = re.compile(r"\d+(?:\.\d+)?(?:\s*(?:%|percent))?")


# ── generation ───────────────────────────────────────────────────────────
def _rng(key: str, n: int) -> random.Random:
    return random.Random(int(hashlib.sha1(f"{key}|{n}".encode()).hexdigest(), 16))


def _words(clause: str, k: int = 5) -> List[str]:
    out, seen = [], set()
    for w in re.findall(r"[A-Za-z][A-Za-z'-]+", clause):
        lw = w.lower()
        if lw in retrieval.STOP or len(lw) < 4 or retrieval.stem(lw) in seen:
            continue
        seen.add(retrieval.stem(lw))
        out.append(lw)
    return out[:k]


def ai_items(ai: dict, evidence: List[dict]) -> List[dict]:
    """Study items from the validated AI artifact: understanding-level flashcards and 4-option questions, each linked to
    the evidence it was written from (literal quotes, re-verified by the store)."""
    by_id = {e["id"]: e for e in evidence}
    recs = []
    for f in ai.get("flashcards") or []:
        ids = [i for i in f["evidence_ids"] if i in by_id]
        if not ids:
            continue
        recs.append({"kind": "flashcard", "subtype": "ai_" + (f.get("type") or "explain"), "difficulty": "medium",
                     "prompt": f["question"][:MAX_PROMPT], "answer": f["answer"][:MAX_ANSWER], "explanation": None,
                     "options": None, "correct_option": None, "key_points": None, "knowledge_id": None, "concept_key": None,
                     "slide_index": by_id[ids[0]].get("slide_index"),
                     "dedupe_key": hashlib.sha1(f"ai_card|{ai_study._norm(f['question'])}".encode()).hexdigest()[:16],
                     "evidence": ai_study.links(ids[:3], by_id)})
    for q in ai.get("quiz") or []:
        ids = [i for i in q["evidence_ids"] if i in by_id]
        if not ids:
            continue
        opts = [{"id": OPTION_LETTERS[i], "text": o} for i, o in enumerate(q["options"])]
        recs.append({"kind": "mcq", "subtype": "ai_quiz", "difficulty": "medium", "prompt": q["question"][:MAX_PROMPT],
                     "answer": q["options"][q["correct_index"]][:MAX_ANSWER], "explanation": q.get("explanation") or None,
                     "options": opts, "correct_option": OPTION_LETTERS[q["correct_index"]], "key_points": None,
                     "knowledge_id": None, "concept_key": None, "slide_index": by_id[ids[0]].get("slide_index"),
                     "dedupe_key": hashlib.sha1(f"ai_mcq|{ai_study._norm(q['question'])}".encode()).hexdigest()[:16],
                     "evidence": ai_study.links(ids[:3], by_id)})
    return recs


def _link(k: dict, role: str = "answer") -> dict:
    e = k["evidence"][0]
    return {"evidence_id": e["evidence_id"], "quote": e["quote"], "role": role}


def build_items(items: List[dict], regen: Optional[Dict[str, int]] = None) -> Tuple[List[dict], List[dict]]:
    """knowledge items -> (study item records, skipped reasons). Pure; `regen[dedupe_key]` varies MCQ distractors."""
    regen = regen or {}
    recs: List[dict] = []
    skipped: List[dict] = []
    concept_title = {k["concept_key"]: k["title"] for k in items if k["kind"] == "concept" and k["concept_key"]}
    defs = []
    for k in items:
        if k["kind"] == "definition":
            p = knowledge.parse_definition(k["body"] or "")
            if p:
                defs.append((k, p[0], p[1]))

    def rec(k, subtype, kind, difficulty, prompt, answer, links, explanation=None, **extra):
        key = hashlib.sha1(f"{subtype}|{k['dedupe_key']}".encode()).hexdigest()[:16]
        recs.append({"kind": kind, "subtype": subtype, "difficulty": difficulty, "prompt": prompt[:MAX_PROMPT], "answer": answer[:MAX_ANSWER],
                     "explanation": explanation, "knowledge_id": k["id"], "concept_key": k.get("concept_key"), "slide_index": k.get("slide_index"),
                     "dedupe_key": key, "evidence": links, **extra})
        return key

    terms_pool = [(k, t) for k, t, _ in defs] + [(k, k["title"]) for k in items if k["kind"] == "concept" and not any(d[0]["concept_key"] == k["concept_key"] for d in defs)]

    for k, term, clause in defs:
        sent = k["body"]
        expl = f"The lecture says: “{sent}”"
        rec(k, "definition", "flashcard", "easy", f"What is {term}?", sent, [_link(k)])
        m = re.search(re.escape(term), sent, re.I)
        if m:
            rec(k, "cloze", "flashcard", "medium", sent[:m.start()] + "_____" + sent[m.end():], m.group(0), [_link(k)], expl)
        rec(k, "explain", "short_answer", "hard", f"Explain {term} as it was described in the lecture.", sent, [_link(k)], expl,
            key_points=_words(clause))
        # MCQ: which description fits the term?  (distractors = the lecture's other definitions)
        others = [(o, t, c) for o, t, c in defs if o["id"] != k["id"] and knowledge.key_of(t) != knowledge.key_of(term)]
        if len(others) >= 2:
            dk = hashlib.sha1(f"mcq_definition|{k['dedupe_key']}".encode()).hexdigest()[:16]
            r = _rng(dk, regen.get(dk, 0))
            pick = sorted(others, key=lambda x: (x[0].get("slide_index") == k.get("slide_index"), x[0]["id"]))     # prefer other slides
            pick = r.sample(pick, min(3, len(pick)))
            opts = [{"text": _cap(clause), "src": k, "ok": True}] + [{"text": _cap(c), "src": o, "ok": False} for o, _, c in pick]
            r.shuffle(opts)
            rec(k, "mcq_definition", "mcq", "medium", f"Which of these best describes {term}?", _cap(clause),
                [_link(k)] + [_link(o["src"], "distractor") for o in opts if not o["ok"]], expl,
                options=[{"id": OPTION_LETTERS[i], "text": o["text"]} for i, o in enumerate(opts)],
                correct_option=OPTION_LETTERS[next(i for i, o in enumerate(opts) if o["ok"])])
        else:
            skipped.append({"subtype": "mcq_definition", "knowledge_id": k["id"], "reason": "needs at least 3 distinct defined concepts in this lecture"})
        # MCQ: which term does this statement define?
        others_t = [(o, t) for o, t in terms_pool if knowledge.key_of(t) != knowledge.key_of(term)]
        seen_t, uniq = set(), []
        for o, t in others_t:
            if knowledge.key_of(t) not in seen_t:
                seen_t.add(knowledge.key_of(t))
                uniq.append((o, t))
        if len(uniq) >= 2:
            dk = hashlib.sha1(f"mcq_term|{k['dedupe_key']}".encode()).hexdigest()[:16]
            r = _rng(dk, regen.get(dk, 0))
            pick = r.sample(uniq, min(3, len(uniq)))
            opts = [{"text": _cap(term), "src": k, "ok": True}] + [{"text": _cap(t), "src": o, "ok": False} for o, t in pick]
            r.shuffle(opts)
            rec(k, "mcq_term", "mcq", "easy", f"Which term does the lecture describe this way: “{_cap(clause)}”?", _cap(term),
                [_link(k)] + [_link(o["src"], "distractor") for o in opts if not o["ok"]], expl,
                options=[{"id": OPTION_LETTERS[i], "text": o["text"]} for i, o in enumerate(opts)],
                correct_option=OPTION_LETTERS[next(i for i, o in enumerate(opts) if o["ok"])])
        else:
            skipped.append({"subtype": "mcq_term", "knowledge_id": k["id"], "reason": "needs at least 3 distinct concepts in this lecture"})

    for k in items:
        c = concept_title.get(k.get("concept_key"))
        body = k["body"] or ""
        start = k["start"]
        spoken = start is not None                                      # written (slide / document) items have no timestamp
        if k["kind"] == "formula" and k["title"] != "Spoken formula":
            rec(k, "formula", "flashcard", "medium", f"State the formula the lecture gives for “{k['title']}”.", body, [_link(k)])
        elif k["kind"] == "example":
            rec(k, "example", "flashcard", "medium", f"Give an example from the lecture related to {c}." if c else
                (f"What example did the professor give around {mmss(start)}?" if spoken else "What example does the lecture material give?"), body, [_link(k)])
        elif k["kind"] == "warning":
            rec(k, "warning", "flashcard", "medium", (f"What did the professor warn about regarding {c}?" if spoken else
                f"What does the lecture material warn about regarding {c}?") if c else
                (f"What warning did the professor give around {mmss(start)}?" if spoken else "What warning does the lecture material give?"), body, [_link(k)])
        elif k["kind"] == "emphasis":
            if "exam" in k["title"].lower():
                rec(k, "exam", "flashcard", "medium", "What did the professor say would be on the exam?" if spoken else
                    "What does the lecture material say about the exam?", body, [_link(k)])
            elif c:
                rec(k, "emphasis", "flashcard", "medium", f"What did the professor stress about {c}?" if spoken else
                    f"What does the lecture material stress about {c}?", body, [_link(k)])
        elif k["kind"] == "fact":
            m = _NUM.search(body)
            if m:
                rec(k, "fact_cloze", "flashcard", "medium", body[:m.start()] + "_____" + body[m.end():], m.group(0), [_link(k)])
            elif c:
                rec(k, "fact", "flashcard", "medium", f"According to the lecture, what can you say about {c}?", body, [_link(k)])
    return recs, skipped


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


# ── service ──────────────────────────────────────────────────────────────
class StudyService:
    def __init__(self, repo: LectureRepository, storage: StorageService):
        self.repo, self.storage = repo, storage
        self.ws = WorkspaceService(repo, storage)

    # access ------------------------------------------------------------
    def _ready(self, user_id: int, lecture_id: int) -> dict:
        return self.ws._ready(user_id, lecture_id)

    def _item(self, user_id: int, item_id: int) -> dict:
        it = self.repo.get_study_item(item_id)
        if not it:
            raise NotFoundError("not found")
        self._ready(user_id, it["lecture_id"])                                   # same authz as the lecture itself
        return it

    def _knowledge_item(self, user_id: int, kid: int) -> dict:
        k = self.repo.get_knowledge(kid)
        if not k:
            raise NotFoundError("not found")
        self._ready(user_id, k["lecture_id"])
        return k

    # presentation --------------------------------------------------------
    def _links(self, lecture_id: int, links: List[dict]) -> List[dict]:
        live = {i["evidence_id"]: i for i in self.ws.present_evidence(lecture_id, [x["evidence_id"] for x in links])}
        out = []
        for ln in links:
            it = live.get(ln["evidence_id"])
            out.append({**it, "role": ln["role"], "quote": ln["quote"], "available": True} if it else
                       {"evidence_id": ln["evidence_id"], "role": ln["role"], "quote": ln["quote"], "available": False,
                        "note": "This source was replaced when the lecture was re-processed."})
        return out

    def present_knowledge(self, k: dict, user_id: Optional[int] = None, reviewed: Optional[dict] = None) -> dict:
        ev = self._links(k["lecture_id"], k["evidence"])
        first = next((e for e in ev if e.get("available")), None)
        out = {"knowledge_id": k["id"], "lecture_id": k["lecture_id"], "kind": k["kind"], "title": k["title"], "body": k["body"],
               "concept_key": k["concept_key"], "slide_index": k["slide_index"], "start": k["start"], "end": k["end"],
               "seek_seconds": first["citation"]["seek_seconds"] if first else None, "confidence": k["confidence"], "method": k["method"],
               "origin": "notiv_generated", "origin_label": "Notiv study note", "evidence": ev,
               "stale": any(not e.get("available") for e in ev), "reviewed": reviewed}
        if (k["method"] or "").startswith("ai:"):
            out.update(origin="ai_generated", origin_label="AI study note, cited to your source")
            if k["kind"] == "concept":
                ai = self._ai_concepts(k["lecture_id"]).get(k["concept_key"]) or {}
                # body = what the SOURCE says (cited); the explanation is supplementary and labelled as not from the source
                out.update(source_supported=bool(k["body"]), supplementary_explanation=ai.get("explanation") or None,
                           supplementary_label="Supplementary explanation (AI): not from your notes",
                           not_in_source_note=None if k["body"] else "Your notes name this concept but do not explain it.")
        return out

    def _ai_concepts(self, lecture_id: int) -> dict:
        cache = self.__dict__.setdefault("_ai_cache", {})
        if lecture_id not in cache:
            ai = ai_study.load(self.repo, lecture_id) or {}
            cache[lecture_id] = {c["key"]: c for c in ai.get("concepts") or []}
        return cache[lecture_id]

    def present_item(self, it: dict, reveal: bool, user_id: Optional[int] = None) -> dict:
        out = {"item_id": it["id"], "lecture_id": it["lecture_id"], "kind": it["kind"], "subtype": it["subtype"], "difficulty": it["difficulty"],
               "prompt": it["prompt"], "options": it["options"], "slide_index": it["slide_index"], "concept_key": it["concept_key"],
               "knowledge_id": it["knowledge_id"], "origin": it["origin"], "verified": it["origin"] == "generated",
               "regen_count": it["regen_count"], "updated_at": it["updated_at"]}
        if reveal:
            ev = self._links(it["lecture_id"], it["evidence"])
            out.update(answer=it["answer"], explanation=it["explanation"], correct_option=it["correct_option"], key_points=it["key_points"],
                       evidence=ev, stale=any(not e.get("available") for e in ev))
        return out

    # knowledge ---------------------------------------------------------
    def _ensure_knowledge(self, lecture_id: int) -> None:
        knowledge.ensure(self.repo, lecture_id)

    def knowledge(self, user_id: int, lecture_id: int, kind: Optional[str] = None, slide: Optional[int] = None, q: Optional[str] = None) -> dict:
        self._ready(user_id, lecture_id)
        if kind is not None and kind not in knowledge.KINDS:
            raise ValidationError(f"kind must be one of {', '.join(knowledge.KINDS)}")
        self._ensure_knowledge(lecture_id)
        rows = self.repo.search_knowledge([lecture_id], q, 50) if q and q.strip() else self.repo.list_knowledge([lecture_id], kind)
        if q and kind:
            rows = [r for r in rows if r["kind"] == kind]
        if slide is not None:
            rows = [r for r in rows if r["slide_index"] == slide]
        rev = self.repo.reviewed_map(user_id, [r["id"] for r in rows])
        counts: Dict[str, int] = {}
        for r in self.repo.list_knowledge([lecture_id]):
            counts[r["kind"]] = counts.get(r["kind"], 0) + 1
        return {"lecture_id": lecture_id, "counts": counts, "stale": knowledge.is_stale(self.repo, lecture_id),
                "items": [self.present_knowledge(r, reviewed=rev.get(r["id"])) for r in rows]}

    def concepts(self, user_id: int, lecture_id: int) -> dict:
        """Concept review view: each concept with its definition, formulas, examples, warnings, facts and emphasis."""
        self._ready(user_id, lecture_id)
        self._ensure_knowledge(lecture_id)
        rows = self.repo.list_knowledge([lecture_id])
        rev = self.repo.reviewed_map(user_id, [r["id"] for r in rows])
        by_key: Dict[str, List[dict]] = {}
        for r in rows:
            if r["kind"] != "concept" and r["concept_key"]:
                by_key.setdefault(r["concept_key"], []).append(r)
        out = []
        for c in (r for r in rows if r["kind"] == "concept"):
            related = by_key.get(c["concept_key"], [])
            pres = lambda r: self.present_knowledge(r, reviewed=rev.get(r["id"]))            # noqa: E731
            out.append({**pres(c), "definitions": [pres(r) for r in related if r["kind"] == "definition"],
                        "formulas": [pres(r) for r in related if r["kind"] == "formula"],
                        "examples": [pres(r) for r in related if r["kind"] == "example"],
                        "warnings": [pres(r) for r in related if r["kind"] == "warning"],
                        "emphasis": [pres(r) for r in related if r["kind"] == "emphasis"],
                        "facts": [pres(r) for r in related if r["kind"] == "fact"]})
        loose = [self.present_knowledge(r, reviewed=rev.get(r["id"])) for r in rows if r["kind"] != "concept" and not r["concept_key"]]
        return {"lecture_id": lecture_id, "concepts": out, "unlinked": loose, "stale": knowledge.is_stale(self.repo, lecture_id)}

    def regenerate_knowledge(self, user_id: int, lecture_id: int) -> dict:
        self._ready(user_id, lecture_id)
        ai_status = ai_study.ensure(self.repo, lecture_id, max_wait=120)      # AI material first when missing / stale
        r = knowledge.regenerate(self.repo, lecture_id)
        r["ai_study"] = {k: ai_status[k] for k in ("status", "message", "provider", "model") if k in ai_status}
        return {"lecture_id": lecture_id, **r, "stale": False,
                "status": "ok" if r["total"] else "insufficient_evidence",
                "message": None if r["total"] else "This lecture's evidence doesn't contain extractable concepts, definitions or facts yet."}

    def delete_knowledge(self, user_id: int, kid: int) -> dict:
        self._knowledge_item(user_id, kid)
        return {"deleted": self.repo.delete_knowledge(kid), "knowledge_id": kid}

    def review_concept(self, user_id: int, kid: int) -> dict:
        self._knowledge_item(user_id, kid)
        return {"knowledge_id": kid, **self.repo.mark_reviewed(user_id, kid)}

    # study items ---------------------------------------------------------
    def generate_items(self, user_id: int, lecture_id: int, kinds: Optional[Sequence[str]] = None) -> dict:
        self._ready(user_id, lecture_id)
        ai_study.ensure(self.repo, lecture_id, max_wait=120)                 # explicit action: build AI material if missing
        return self._generate(lecture_id, kinds)

    def _generate(self, lecture_id: int, kinds: Optional[Sequence[str]] = None) -> dict:
        if kinds is not None:
            bad = [k for k in kinds if k not in KINDS]
            if bad or not kinds:
                raise ValidationError(f"kinds must be a non-empty subset of {', '.join(KINDS)}")
        self._ensure_knowledge(lecture_id)
        ai = ai_study.load(self.repo, lecture_id)
        if ai:
            recs, skipped = ai_items(ai, ai_study.usable_evidence(self.repo, lecture_id)), []
        else:
            recs, skipped = build_items(self.repo.list_knowledge([lecture_id]))
        if kinds:
            recs = [r for r in recs if r["kind"] in kinds]
        r = self.repo.sync_study_items(lecture_id, recs, knowledge.fingerprint(self.repo, lecture_id), list(kinds) if kinds else None)
        active = len(self.repo.list_study_items([lecture_id]))
        seen, skip = set(), []
        for s in skipped:                                               # collapse per-subtype reasons
            if s["subtype"] not in seen and (not kinds or "mcq" in kinds):
                seen.add(s["subtype"])
                skip.append({"subtype": s["subtype"], "reason": s["reason"]})
        return {"lecture_id": lecture_id, "created": r["created"], "updated": r["updated"], "removed": r["removed"], "active_items": active,
                "skipped": skip, "status": "ok" if active else "insufficient_evidence",
                "message": None if active else "This lecture doesn't contain enough definitions, formulas or facts to build study items yet."}

    def list_items(self, user_id: int, lecture_id: int, kind: Optional[str] = None, difficulty: Optional[str] = None, reveal: bool = False) -> dict:
        self._ready(user_id, lecture_id)
        if kind is not None and kind not in KINDS:
            raise ValidationError(f"kind must be one of {', '.join(KINDS)}")
        if difficulty is not None and difficulty not in DIFFICULTIES:
            raise ValidationError(f"difficulty must be one of {', '.join(DIFFICULTIES)}")
        meta = self.repo.study_meta(lecture_id)
        if not meta:                                                     # first visit: build (a later empty list stays empty until regenerate)
            self._generate(lecture_id)
        elif meta["fingerprint"] != knowledge.fingerprint(self.repo, lecture_id) and ai_study.load(self.repo, lecture_id):
            self._generate(lecture_id)                                   # AI study material arrived: replace rule-based items
        meta = self.repo.study_meta(lecture_id)
        items = self.repo.list_study_items([lecture_id], kind, difficulty)
        return {"lecture_id": lecture_id, "stale": bool(meta) and meta["fingerprint"] != knowledge.fingerprint(self.repo, lecture_id),
                "count": len(items), "items": [self.present_item(i, reveal) for i in items]}

    def get_item(self, user_id: int, item_id: int) -> dict:
        return self.present_item(self._item(user_id, item_id), reveal=True)

    def delete_item(self, user_id: int, item_id: int) -> dict:
        self._item(user_id, item_id)
        return {"item_id": item_id, "deleted": self.repo.delete_study_item(item_id)}

    def edit_item(self, user_id: int, item_id: int, patch: dict) -> dict:
        it = self._item(user_id, item_id)
        if not isinstance(patch, dict) or not patch:
            raise ValidationError("nothing to change")
        unknown = set(patch) - {"prompt", "answer", "explanation", "difficulty", "options", "correct_option", "key_points", "evidence"}
        if unknown:
            raise ValidationError(f"cannot edit: {', '.join(sorted(unknown))}")
        fields: dict = {}
        for k, mx in (("prompt", MAX_PROMPT), ("answer", MAX_ANSWER), ("explanation", MAX_ANSWER)):
            if k in patch:
                v = patch[k]
                if v is None and k == "explanation":
                    fields[k] = None
                    continue
                if not isinstance(v, str) or not v.strip() or len(v) > mx:
                    raise ValidationError(f"{k} must be 1-{mx} characters")
                fields[k] = v.strip()
        if "difficulty" in patch:
            if patch["difficulty"] not in DIFFICULTIES:
                raise ValidationError(f"difficulty must be one of {', '.join(DIFFICULTIES)}")
            fields["difficulty"] = patch["difficulty"]
        if "key_points" in patch:
            kp = patch["key_points"]
            if not isinstance(kp, list) or len(kp) > 8 or not all(isinstance(x, str) and 0 < len(x) <= 80 for x in kp):
                raise ValidationError("key_points must be up to 8 short strings")
            fields["key_points"] = kp
        if it["kind"] == "mcq":
            if "answer" in fields:
                raise ValidationError("for multiple-choice items edit options / correct_option instead of answer")
            if "options" in patch or "correct_option" in patch:
                opts = patch.get("options", it["options"])
                if not isinstance(opts, list) or not 2 <= len(opts) <= 6 or not all(isinstance(o, dict) and isinstance(o.get("id"), str) and
                                                                                    isinstance(o.get("text"), str) and 0 < len(o["text"]) <= 300 for o in opts):
                    raise ValidationError("options must be 2-6 items with id and text")
                if len({o["id"] for o in opts}) != len(opts):
                    raise ValidationError("option ids must be unique")
                correct = patch.get("correct_option", it["correct_option"])
                if correct not in {o["id"] for o in opts}:
                    raise ValidationError("correct_option must be one of the option ids")
                fields["options"] = [{"id": o["id"], "text": o["text"].strip()} for o in opts]
                fields["correct_option"] = correct
                fields["answer"] = next(o["text"] for o in fields["options"] if o["id"] == correct)
        elif "options" in patch or "correct_option" in patch:
            raise ValidationError("only multiple-choice items have options")
        links = None
        if "evidence" in patch:
            ev = patch["evidence"]
            if not isinstance(ev, list) or not ev or not all(isinstance(e, dict) and isinstance(e.get("evidence_id"), int) for e in ev):
                raise ValidationError("evidence must be a non-empty list of {evidence_id, quote?}")
            links = [{"evidence_id": e["evidence_id"], "quote": e.get("quote") if isinstance(e.get("quote"), str) else None, "role": "answer"} for e in ev]
        try:
            self.repo.update_study_item(item_id, fields, links)
        except ValueError as e:                                           # foreign evidence / quote not in evidence
            raise ValidationError(str(e))
        return self.present_item(self.repo.get_study_item(item_id), reveal=True)

    def regenerate_item(self, user_id: int, item_id: int) -> dict:
        """Rebuilds ONE item from the lecture's current knowledge (new distractors for MCQs). Discards user edits
        to that item; keeps its id, so progress is preserved."""
        it = self._item(user_id, item_id)
        if not it["knowledge_id"] or not self.repo.get_knowledge(it["knowledge_id"]):
            raise ConflictError("the lecture concept this item was built from no longer exists", "source_missing")
        recs, _ = build_items(self.repo.list_knowledge([it["lecture_id"]]), {it["dedupe_key"]: it["regen_count"] + 1})
        rec = next((r for r in recs if r["dedupe_key"] == it["dedupe_key"]), None)
        if rec is None:
            raise ConflictError("this item can no longer be generated from the current lecture evidence", "source_missing")
        try:
            self.repo.replace_item_content(item_id, rec)
        except ValueError as e:
            raise ConflictError(str(e), "source_invalid")
        return self.present_item(self.repo.get_study_item(item_id), reveal=True)

    # scopes ----------------------------------------------------------------
    def course_lectures(self, user_id: int, course_id: int) -> Tuple[dict, List[int]]:
        """(course, ids of PROCESSED lectures of exactly this course, same workspace). Missing / not-yours -> NotFound."""
        course = authz.get_course(self.repo, user_id, course_id)
        ids = []
        for lid in self.repo.course_lecture_ids(course_id, only_done=True):
            lec = self.repo.get_lecture(lid)
            if lec and lec["course_id"] == course_id and lec["workspace_id"] == course["workspace_id"]:
                ids.append(lid)
        return course, ids

    def _scope(self, user_id: int, scope_type: str, scope_id: int) -> List[int]:
        if scope_type == "lecture":
            self._ready(user_id, scope_id)
            return [scope_id]
        if scope_type == "course":
            return self.course_lectures(user_id, scope_id)[1]
        raise ValidationError("bad scope")

    def _ensure_items(self, lecture_ids: Sequence[int]) -> None:
        for lid in lecture_ids:
            if not self.repo.study_meta(lid):
                self._generate(lid)

    # sessions --------------------------------------------------------------
    def start_session(self, user_id: int, scope_type: str, scope_id: int, kinds: Optional[Sequence[str]] = None,
                      difficulty: Optional[str] = None, limit: int = DEFAULT_SESSION_ITEMS, include_mastered: bool = False) -> dict:
        lids = self._scope(user_id, scope_type, scope_id)
        if kinds is not None and (not isinstance(kinds, (list, tuple)) or not kinds or any(k not in KINDS for k in kinds)):
            raise ValidationError(f"kinds must be a non-empty subset of {', '.join(KINDS)}")
        if difficulty is not None and difficulty not in DIFFICULTIES:
            raise ValidationError(f"difficulty must be one of {', '.join(DIFFICULTIES)}")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_SESSION_ITEMS:
            raise ValidationError(f"limit must be 1-{MAX_SESSION_ITEMS}")
        self._ensure_items(lids)
        items = [i for i in self.repo.list_study_items(lids, None, difficulty) if not kinds or i["kind"] in kinds]
        if not items:
            raise ValidationError("no study items match; the lecture(s) may not contain enough evidence to generate any")
        prog = self.repo.progress_rows(user_id, [i["id"] for i in items])
        mastered = lambda i: prog.get(i["id"], {}).get("streak", 0) >= MASTERY_STREAK            # noqa: E731
        pool = items if include_mastered else [i for i in items if not mastered(i)]
        review = include_mastered or not pool
        pool = pool or items

        def prio(i):
            p = prog.get(i["id"])
            return (p is not None, p["streak"] if p else 0, (p["correct_count"] / p["attempts"]) if p else 0, p["last_attempt_at"] if p else 0, i["id"])
        chosen = sorted(pool, key=prio)[:limit]
        sid = self.repo.create_session(user_id, scope_type, scope_id, {"kinds": list(kinds) if kinds else None, "difficulty": difficulty,
                                                                       "limit": limit, "review_mastered": review}, [i["id"] for i in chosen])
        return self.session(user_id, sid)

    def _own_session(self, user_id: int, session_id: int) -> dict:
        s = self.repo.get_session(session_id)
        if not s or s["user_id"] != user_id:
            raise NotFoundError("not found")                                          # someone else's session == missing
        self._scope(user_id, s["scope_type"], s["scope_id"])                          # access may have been revoked since
        return s

    def _reveal(self, it: dict, attempt: Optional[dict] = None) -> dict:
        out = self.present_item(it, reveal=True)
        r = {"answer": out["answer"], "explanation": out["explanation"], "correct_option": out["correct_option"],
             "key_points": out["key_points"], "evidence": out["evidence"], "verified": out["verified"]}
        if attempt and it["kind"] == "short_answer" and attempt.get("response_text"):
            have = {retrieval.stem(w) for w in re.findall(r"[a-z]+", attempt["response_text"].lower())}
            r["key_points_hit"] = [k for k in (it["key_points"] or []) if retrieval.stem(k) in have]
        return r

    def _lecture_ids_of(self, item_ids: Sequence[int]) -> List[int]:
        from . import store
        if not item_ids:
            return []
        ph = ",".join("?" * len(item_ids))
        return [r[0] for r in store.conn().execute(f"SELECT DISTINCT lecture_id FROM study_items WHERE id IN ({ph})", list(item_ids))]

    def session(self, user_id: int, session_id: int) -> dict:
        s = self._own_session(user_id, session_id)
        ids = self.repo.session_item_ids(session_id)
        by_id = {i["id"]: i for i in self.repo.list_study_items(self._lecture_ids_of(ids), ids=ids)} if ids else {}
        att = self.repo.session_attempts(session_id)
        items = []
        for iid in ids:
            it = by_id.get(iid)
            if not it:
                continue
            a = att.get(iid)
            p = self.present_item(it, reveal=False)
            p["attempted"] = a is not None
            p["correct"] = None if a is None else bool(a["correct"])
            if a:
                p["selected_option"], p["self_grade"] = a["selected_option"], a["self_grade"]
                p["reveal"] = self._reveal(it, a)
            items.append(p)
        answered = sum(1 for i in items if i["attempted"])
        correct = sum(1 for i in items if i["correct"])
        return {"session_id": session_id, "scope": {"type": s["scope_type"], "id": s["scope_id"]}, "config": s["config"],
                "created_at": s["created_at"], "finished_at": s["finished_at"], "items": items,
                "summary": {"total": len(items), "answered": answered, "correct": correct, "remaining": len(items) - answered,
                            "complete": bool(items) and answered == len(items),
                            "accuracy": round(correct / answered, 3) if answered else None}}

    def attempt(self, user_id: int, session_id: int, item_id: int, selected_option: Optional[str] = None,
                self_grade: Optional[str] = None, response_text: Optional[str] = None) -> dict:
        self._own_session(user_id, session_id)
        if item_id not in self.repo.session_item_ids(session_id):
            raise NotFoundError("not found")
        it = self.repo.get_study_item(item_id)
        if not it:
            raise NotFoundError("not found")
        if response_text is not None:
            if not isinstance(response_text, str) or len(response_text) > MAX_RESPONSE:
                raise ValidationError(f"response_text must be at most {MAX_RESPONSE} characters")
            response_text = response_text.strip() or None
        if it["kind"] == "mcq":
            if selected_option not in {o["id"] for o in (it["options"] or [])}:
                raise ValidationError("selected_option must be one of the item's option ids")
            correct, self_grade = selected_option == it["correct_option"], None
        else:
            if self_grade not in GRADES:
                raise ValidationError(f"self_grade must be one of {', '.join(GRADES)}")
            correct, selected_option = self_grade == "know", None
        try:
            self.repo.add_attempt(session_id, item_id, user_id, correct, self_grade, selected_option, response_text)
        except ValueError:
            raise ConflictError("this item was already answered in this session", "already_answered")
        a = self.repo.session_attempts(session_id)[item_id]
        p = self.repo.progress_rows(user_id, [item_id])[item_id]
        return {"session_id": session_id, "item_id": item_id, "correct": correct, "self_grade": self_grade,
                "selected_option": selected_option, "reveal": self._reveal(it, a),
                "item_progress": {"attempts": p["attempts"], "streak": p["streak"], "mastered": p["streak"] >= MASTERY_STREAK},
                "summary": self.session(user_id, session_id)["summary"]}

    def finish(self, user_id: int, session_id: int) -> dict:
        self._own_session(user_id, session_id)
        self.repo.finish_session(session_id)
        return self.session(user_id, session_id)

    # progress --------------------------------------------------------------
    def progress(self, user_id: int, scope_type: str, scope_id: int) -> dict:
        lids = self._scope(user_id, scope_type, scope_id)
        items = self.repo.list_study_items(lids)
        prog = self.repo.progress_rows(user_id, [i["id"] for i in items])
        concepts = [k for lid in lids for k in self.repo.list_knowledge([lid], "concept")]
        reviewed = self.repo.reviewed_map(user_id, [k["id"] for k in concepts])
        tot_att = sum(p["attempts"] for p in prog.values())
        tot_ok = sum(p["correct_count"] for p in prog.values())

        def bucket(rows):
            n = len(rows)
            a = sum(1 for i in rows if i["id"] in prog)
            m = sum(1 for i in rows if prog.get(i["id"], {}).get("streak", 0) >= MASTERY_STREAK)
            return {"total": n, "attempted": a, "mastered": m, "completion_pct": round(100 * a / n) if n else 0}
        n_rev = sum(1 for k in concepts if k["id"] in reviewed)
        return {"scope": {"type": scope_type, "id": scope_id}, **bucket(items),
                "accuracy": round(tot_ok / tot_att, 3) if tot_att else None, "total_attempts": tot_att,
                "by_kind": {k: bucket([i for i in items if i["kind"] == k]) for k in KINDS},
                "by_lecture": {str(l): bucket([i for i in items if i["lecture_id"] == l]) for l in lids} if scope_type == "course" else None,
                "concepts": {"total": len(concepts), "reviewed": n_rev, "completion_pct": round(100 * n_rev / len(concepts)) if concepts else 0},
                "sessions": self.repo.session_counts(user_id, scope_type, scope_id)}
