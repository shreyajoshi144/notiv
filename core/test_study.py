"""Sprint 4 service-level tests: knowledge extraction, study item generation, edit/delete/regenerate, sessions and
progress, course memory, grounding enforcement, isolation. Real lectures via core.study_world (real pipeline)."""
import unittest

from core import study_fixtures as F
from core.lecture import knowledge, store, study_store
from core.lecture.course_memory import CourseMemory
from core.lecture.errors import ConflictError, NotFoundError, ValidationError
from core.lecture.study import StudyService
from core.lecture.study_store import norm
from core.study_world import LAZY_WORLD

W = LAZY_WORLD
from core.study_world import ALICE, BOB, EVE  # noqa: E402
class _Lazy:
    """Built on first use (inside a test), not at import: keeps collection free of fixture generation."""
    def __init__(self, make):
        self._make, self._obj = make, None

    def __getattr__(self, name):
        if self._obj is None:
            self._obj = self._make()
        return getattr(self._obj, name)


S = _Lazy(lambda: StudyService(W.repo, W.storage))
C = _Lazy(lambda: CourseMemory(W.repo, W.storage))


def fresh_sparse(title):
    """A new two-definition lecture for tests that mutate it (shared world lectures stay pristine)."""
    from core.lecture.align import Segment
    return W.new_lecture(ALICE, title, [Segment(0.5, 3.0, "A vector is defined as an ordered list of numbers."),
                                        Segment(3.0, 6.0, "A matrix is defined as a rectangular array of numbers.")], None, W.c_scratch)


def fresh_empty(title):
    from core.lecture.align import Segment
    return W.new_lecture(ALICE, title, [Segment(0.5, 3.0, "Okay so let us get started."), Segment(3.0, 5.5, "Thanks everyone and see you next week.")],
                         None, W.c_scratch)


def evidence_text(lid):
    return {e["id"]: e["text"] for e in W.repo.list_evidence(lid)}


def all_text(lid):
    return norm(" ".join(evidence_text(lid).values()))


def kn(lid, kind=None):
    return S.knowledge(ALICE, lid, kind)["items"]


def items(lid, reveal=True, **kw):
    return S.list_items(ALICE, lid, reveal=reveal, **kw)["items"]


# ───────────────────────── knowledge extraction ─────────────────────────
class TestKnowledgeExtraction(unittest.TestCase):
    def test_extracts_every_kind_from_a_real_lecture(self):
        counts = S.knowledge(ALICE, W.DL1)["counts"]
        self.assertEqual(counts, {"concept": 7, "definition": 6, "formula": 6, "emphasis": 3, "example": 2, "warning": 2, "fact": 2})

    def test_every_item_is_grounded_in_real_evidence_of_its_own_lecture(self):
        for lid in (W.DL1, W.DL2, W.DB1):
            texts = evidence_text(lid)
            self.assertTrue(texts)
            for k in W.repo.list_knowledge([lid]):
                self.assertTrue(k["evidence"], k["title"])
                for ln in k["evidence"]:
                    self.assertIn(ln["evidence_id"], texts, f"{k['title']} cites evidence outside lecture {lid}")
                    self.assertIn(norm(ln["quote"]), norm(texts[ln["evidence_id"]]), f"quote not in evidence: {ln['quote']!r}")
                if k["body"]:
                    self.assertIn(norm(k["body"]), all_text(lid), f"body not verbatim lecture text: {k['body']!r}")

    def test_definitions_are_the_professors_own_sentences(self):
        d = {k["title"]: k for k in kn(W.DL1, "definition")}
        self.assertEqual(set(d), {"Gradient", "Learning rate", "Backpropagation", "Overfitting", "Regularization", "Convolution"})
        self.assertEqual(d["Gradient"]["body"], F.DL1[0].text)
        self.assertEqual(d["Overfitting"]["body"], F.DL1[6].text)
        self.assertEqual(d["Backpropagation"]["evidence"][0]["role"], "primary")
        self.assertEqual(d["Learning rate"]["evidence"][0]["citation"]["label"], "Slide 1 · 00:03")

    def test_formulas_come_from_slide_text_lines(self):
        f = {k["title"]: k for k in kn(W.DL1, "formula")}
        self.assertEqual(f["Update rule"]["body"], "Update rule: w = w - lr * gradient")
        self.assertEqual(f["Chain rule"]["body"], "Chain rule: dL/dw = dL/dy * dy/dw")
        self.assertEqual(f["Output size"]["slide_index"], 4)
        self.assertTrue(all(k["evidence"][0]["source_type"] == "slide" for k in f.values()))

    def test_examples_warnings_emphasis_and_facts(self):
        self.assertEqual({k["body"] for k in kn(W.DL1, "warning")}, {F.DL1[4].text, F.DL1[12].text})
        self.assertEqual({k["body"] for k in kn(W.DL1, "example")}, {F.DL1[3].text, F.DL1[11].text})
        em = {k["title"].split(":")[0] for k in kn(W.DL1, "emphasis")}
        self.assertEqual(em, {"Emphasis", "Reminder", "Exam relevance"})
        facts = {k["body"] for k in kn(W.DL1, "fact")}
        self.assertEqual(facts, {F.DL1[8].text, F.DL1[9].text})

    def test_concepts_link_slide_titles_and_defined_terms_to_evidence(self):
        cs = {k["title"]: k for k in kn(W.DL1, "concept")}
        self.assertEqual(set(cs), {"Gradient Descent", "Gradient", "Learning rate", "Backpropagation", "Overfitting", "Regularization", "Convolutions"})
        bp = cs["Backpropagation"]
        self.assertEqual({e["origin"] for e in bp["evidence"]}, {"slide_text", "professor_said"})
        self.assertEqual(bp["body"], F.DL1[2].text)
        self.assertIsNone(cs["Gradient Descent"]["body"])                       # slide title only: no invented description
        self.assertEqual(len(cs["Gradient Descent"]["evidence"]), 1)

    def test_concept_review_view_groups_related_knowledge(self):
        c = {x["title"]: x for x in S.concepts(ALICE, W.DL1)["concepts"]}
        self.assertEqual([d["body"] for d in c["Backpropagation"]["definitions"]], [F.DL1[2].text])
        self.assertEqual([f["title"] for f in c["Backpropagation"]["formulas"]], ["Chain rule"])
        self.assertEqual(len(c["Backpropagation"]["examples"]), 1)
        self.assertEqual(len(c["Backpropagation"]["warnings"]), 1)
        self.assertEqual(len(c["Regularization"]["facts"]), 2)

    def test_sentences_that_merely_look_like_definitions_are_not_definitions(self):
        for s in ("This is important: vanishing gradients appear when many small derivatives are multiplied together.",
                  "A common mistake is to apply dropout at test time.", "It is a good idea to start simple.",
                  "Be careful, if the learning rate is too large then training can diverge.",
                  "Weight decay is the most common form, and dropout is another technique.", "The model is a bit slow today.",
                  "We are going to look at three examples today."):
            self.assertIsNone(knowledge.parse_definition(s), s)

    def test_lecture_with_nothing_extractable_yields_nothing_not_filler(self):
        self.assertEqual(S.knowledge(ALICE, W.EMPTY)["items"], [])
        self.assertEqual(S.concepts(ALICE, W.EMPTY)["concepts"], [])
        r = S.regenerate_knowledge(ALICE, W.EMPTY)
        self.assertEqual((r["status"], r["total"]), ("insufficient_evidence", 0))
        self.assertIn("doesn't contain", r["message"])

    def test_filters_and_search(self):
        self.assertEqual({k["kind"] for k in kn(W.DL1, "formula")}, {"formula"})
        self.assertEqual({k["slide_index"] for k in S.knowledge(ALICE, W.DL1, slide=3)["items"]}, {3})
        hits = S.knowledge(ALICE, W.DL1, q="dropout")["items"]
        self.assertTrue(hits and all("dropout" in (k["body"] or "").lower() or "dropout" in k["title"].lower() for k in hits))
        with self.assertRaises(ValidationError):
            S.knowledge(ALICE, W.DL1, kind="bogus")

    def test_knowledge_is_lecture_scoped(self):
        db = " ".join((k["title"] + " " + (k["body"] or "")).lower() for k in kn(W.DB1))
        self.assertIn("primary key", db)
        self.assertNotIn("gradient", db)
        dl = " ".join((k["title"] + " " + (k["body"] or "")).lower() for k in kn(W.DL1))
        self.assertNotIn("primary key", dl)

    def test_presentation_has_clickable_positions_and_provenance(self):
        k = next(x for x in kn(W.DL1, "warning"))
        self.assertEqual((k["origin"], k["origin_label"]), ("notiv_generated", "Notiv study note"))
        self.assertIsNotNone(k["seek_seconds"])
        self.assertEqual(k["evidence"][0]["origin_label"], "Professor said")
        self.assertTrue(k["evidence"][0]["available"])


class TestGroundingEnforcement(unittest.TestCase):
    """The store refuses anything not literally supported — independent of who generated it."""

    def rec(self, ev_id, quote, key="g1", kind="definition"):
        return {"kind": kind, "title": "T", "body": quote, "method": "t", "dedupe_key": key, "evidence": [{"evidence_id": ev_id, "quote": quote}]}

    def test_knowledge_rejects_foreign_evidence_and_fabricated_quotes(self):
        mine = W.repo.list_evidence(W.EMPTY)[0]
        foreign = W.repo.list_evidence(W.DB1)[0]
        with self.assertRaises(ValueError):
            W.repo.sync_knowledge(W.EMPTY, [self.rec(foreign["id"], foreign["text"][:20])], "fp")
        with self.assertRaises(ValueError):
            W.repo.sync_knowledge(W.EMPTY, [self.rec(mine["id"], "Gradient descent was invented in 1847.")], "fp")
        with self.assertRaises(ValueError):
            W.repo.sync_knowledge(W.EMPTY, [self.rec(10 ** 7, "x")], "fp")
        self.assertEqual(W.repo.list_knowledge([W.EMPTY]), [])                      # nothing half-written

    def test_normalisation_allows_case_and_whitespace_only(self):
        e = W.repo.list_evidence(W.SPARSE)[0]
        quote = "  " + e["text"].upper().replace(" ", "  ") + " "
        study_store._check_links(store.conn(), W.SPARSE, [{"evidence_id": e["id"], "quote": quote}])      # same words -> ok
        with self.assertRaises(ValueError):
            study_store._check_links(store.conn(), W.SPARSE, [{"evidence_id": e["id"], "quote": e["text"] + " and more"}])

    def test_study_items_require_valid_evidence(self):
        e = W.repo.list_evidence(W.SPARSE)[0]
        base = {"kind": "flashcard", "subtype": "definition", "difficulty": "easy", "prompt": "p", "answer": "a", "dedupe_key": "zz"}
        foreign = W.repo.list_evidence(W.DB1)[0]
        for ev in ([], [{"evidence_id": foreign["id"], "quote": None}], [{"evidence_id": e["id"], "quote": "invented words"}]):
            with self.assertRaises(ValueError):
                W.repo.sync_study_items(W.SPARSE, [{**base, "evidence": ev}], "fp", ["flashcard"])
        n = len(W.repo.list_study_items([W.SPARSE]))
        self.assertEqual(len(W.repo.list_study_items([W.SPARSE])), n)


# ───────────────────────── study item generation ─────────────────────────
class TestStudyItemGeneration(unittest.TestCase):
    def test_types_subtypes_and_metadata(self):
        its = items(W.DL1)
        by = {}
        for i in its:
            by[(i["kind"], i["subtype"])] = by.get((i["kind"], i["subtype"]), 0) + 1
        self.assertEqual(by, {("flashcard", "definition"): 6, ("flashcard", "cloze"): 6, ("short_answer", "explain"): 6, ("mcq", "mcq_definition"): 6,
                              ("mcq", "mcq_term"): 6, ("flashcard", "formula"): 6, ("flashcard", "example"): 2, ("flashcard", "warning"): 2,
                              ("flashcard", "emphasis"): 2, ("flashcard", "exam"): 1, ("flashcard", "fact_cloze"): 1, ("flashcard", "fact"): 1})
        for i in its:
            self.assertIn(i["kind"], ("flashcard", "short_answer", "mcq"))
            self.assertIn(i["difficulty"], ("easy", "medium", "hard"))
            self.assertTrue(i["prompt"] and i["answer"])
            self.assertEqual((i["origin"], i["verified"]), ("generated", True))
        self.assertEqual({i["difficulty"] for i in its if i["subtype"] == "definition"}, {"easy"})
        self.assertEqual({i["difficulty"] for i in its if i["kind"] == "short_answer"}, {"hard"})

    def test_every_item_cites_real_evidence_and_never_invents_text(self):
        for lid in (W.DL1, W.DL2, W.DB1):
            txt, ev = all_text(lid), evidence_text(lid)
            for i in items(lid):
                self.assertTrue(i["evidence"], i["prompt"])
                for e in i["evidence"]:
                    self.assertTrue(e["available"])
                    self.assertIn(e["evidence_id"], ev)
                    self.assertIn(norm(e["quote"]), norm(ev[e["evidence_id"]]))
                self.assertIn(norm(i["answer"]), txt, f"answer not from the lecture: {i['answer']!r}")          # incl. MCQ correct text
                for opt in i["options"] or []:
                    self.assertIn(norm(opt["text"]), txt, f"option not from the lecture: {opt['text']!r}")
                if i["explanation"]:
                    quoted = i["explanation"].split("“", 1)[1].rsplit("”", 1)[0]
                    self.assertIn(norm(quoted), txt)

    def test_flashcards_and_cloze_reconstruct_the_lecture_sentence(self):
        by_sub = lambda sub: [i for i in items(W.DL1) if i["subtype"] == sub]                  # noqa: E731
        d = {i["prompt"]: i for i in by_sub("definition")}
        self.assertEqual(d["What is gradient?"]["answer"], F.DL1[0].text)                  # term exactly as the professor said it
        sentences = {s.text for s in F.DL1}
        for i in by_sub("cloze") + by_sub("fact_cloze"):
            self.assertIn("_____", i["prompt"])
            self.assertIn(i["prompt"].replace("_____", i["answer"]), sentences)
        self.assertEqual({i["answer"] for i in by_sub("fact_cloze")}, {"50 percent"})

    def test_mcq_options_are_the_lectures_own_other_definitions(self):
        defs = {k["title"].lower(): knowledge.parse_definition(k["body"])[1] for k in kn(W.DL1, "definition")}
        clauses = {norm(c) for c in defs.values()}
        mcqs = [i for i in items(W.DL1) if i["subtype"] == "mcq_definition"]
        self.assertEqual(len(mcqs), 6)
        for i in mcqs:
            term = i["prompt"].replace("Which of these best describes ", "").rstrip("?").lower()
            ids = [o["id"] for o in i["options"]]
            self.assertEqual((len(ids), len(set(ids)), i["correct_option"] in ids), (4, 4, True))
            texts = [norm(o["text"]) for o in i["options"]]
            self.assertEqual(len(set(texts)), 4)
            self.assertTrue(set(texts) <= clauses)                                           # all four are real lecture clauses
            correct = next(o["text"] for o in i["options"] if o["id"] == i["correct_option"])
            self.assertEqual(norm(correct), norm(defs[term]))
            self.assertEqual(sum(e["role"] == "distractor" for e in i["evidence"]), 3)
            self.assertEqual(sum(e["role"] == "answer" for e in i["evidence"]), 1)
        self.assertGreater(len({i["correct_option"] for i in mcqs}), 1)                      # answer position is shuffled

    def test_mcq_term_questions_do_not_leak_the_answer_by_formatting(self):
        for i in (x for x in items(W.DL1) if x["subtype"] == "mcq_term"):
            self.assertTrue(all(o["text"][0].isupper() for o in i["options"]), i["options"])
            self.assertEqual(len({norm(o["text"]) for o in i["options"]}), len(i["options"]))
            self.assertIn(i["answer"], [o["text"] for o in i["options"]])

    def test_short_answer_key_points_come_from_the_answer(self):
        for i in (x for x in items(W.DL1) if x["kind"] == "short_answer"):
            self.assertTrue(1 <= len(i["key_points"]) <= 5)
            for kp in i["key_points"]:
                self.assertIn(kp, i["answer"].lower())

    def test_answers_are_hidden_until_revealed(self):
        hidden = items(W.DL1, reveal=False)
        for i in hidden:
            for f in ("answer", "explanation", "correct_option", "evidence", "key_points"):
                self.assertNotIn(f, i)
        self.assertTrue(all("options" in i for i in hidden))
        full = S.get_item(ALICE, hidden[0]["item_id"])
        self.assertTrue(full["answer"] and full["evidence"])

    def test_insufficient_evidence_is_reported_not_filled(self):
        r = S.generate_items(ALICE, W.EMPTY)
        self.assertEqual((r["status"], r["active_items"], r["created"]), ("insufficient_evidence", 0, 0))
        self.assertIn("enough", r["message"])
        self.assertEqual(S.list_items(ALICE, W.EMPTY)["items"], [])
        with self.assertRaises(ValidationError):
            S.start_session(ALICE, "lecture", W.EMPTY)

    def test_sparse_lecture_skips_mcq_and_says_why(self):
        r = S.generate_items(ALICE, W.SPARSE)
        self.assertEqual(r["status"], "ok")
        kinds = {i["kind"] for i in items(W.SPARSE)}
        self.assertEqual(kinds, {"flashcard", "short_answer"})
        self.assertEqual({s["subtype"] for s in r["skipped"]}, {"mcq_definition", "mcq_term"})
        self.assertTrue(all("at least 3" in s["reason"] for s in r["skipped"]))

    def test_generation_is_idempotent_and_keeps_item_ids(self):
        before = {i["item_id"] for i in items(W.DL2)}
        r = S.generate_items(ALICE, W.DL2)
        self.assertEqual((r["created"], r["removed"]), (0, 0))
        self.assertEqual({i["item_id"] for i in items(W.DL2)}, before)

    def test_kind_filter_and_validation(self):
        self.assertEqual({i["kind"] for i in items(W.DL1, kind="mcq")}, {"mcq"})
        self.assertEqual({i["difficulty"] for i in items(W.DL1, difficulty="hard")}, {"hard"})
        for kw in ({"kind": "essay"}, {"difficulty": "extreme"}):
            with self.assertRaises(ValidationError):
                S.list_items(ALICE, W.DL1, **kw)

    def test_study_content_is_lecture_scoped(self):
        db = " ".join(i["prompt"] + i["answer"] for i in items(W.DB1)).lower()
        self.assertIn("primary key", db)
        for word in ("gradient", "backpropagation", "dropout"):
            self.assertNotIn(word, db)


# ───────────────────────── edit / delete / regenerate ─────────────────────────
class TestEditDeleteRegenerate(unittest.TestCase):
    def one(self, kind="flashcard", subtype="definition", lid=None):
        return next(i for i in items(lid or W.M) if i["kind"] == kind and i["subtype"] == subtype)

    def test_edit_marks_item_edited_and_survives_regeneration(self):
        it = self.one(subtype="formula")
        e = S.edit_item(ALICE, it["item_id"], {"prompt": "Write the update rule.", "difficulty": "hard", "explanation": "My note."})
        self.assertEqual((e["prompt"], e["difficulty"], e["origin"], e["verified"], e["explanation"]), ("Write the update rule.", "hard", "edited", False, "My note."))
        n = len(items(W.M))
        r = S.generate_items(ALICE, W.M)
        self.assertEqual((r["created"], r["removed"], len(items(W.M))), (0, 0, n))
        again = S.get_item(ALICE, it["item_id"])
        self.assertEqual((again["prompt"], again["origin"]), ("Write the update rule.", "edited"))      # not overwritten
        self.assertTrue(again["evidence"])                                                              # still cited

    def test_edit_validation(self):
        it = self.one(subtype="example")["item_id"]
        for patch in ({}, {"prompt": ""}, {"prompt": "x" * 501}, {"answer": "y" * 2001}, {"difficulty": "nightmare"}, {"bogus": 1},
                      {"key_points": ["a"] * 9}, {"options": [{"id": "a", "text": "x"}, {"id": "b", "text": "y"}]}, {"correct_option": "a"}, "text"):
            with self.assertRaises(ValidationError, msg=str(patch)[:40]):
                S.edit_item(ALICE, it, patch)

    def test_mcq_edit_keeps_options_and_answer_consistent(self):
        m = self.one("mcq", "mcq_definition")
        full = S.get_item(ALICE, m["item_id"])
        opts = [{"id": o["id"], "text": o["text"]} for o in full["options"]]
        other = next(o["id"] for o in opts if o["id"] != full["correct_option"])
        e = S.edit_item(ALICE, m["item_id"], {"correct_option": other})
        self.assertEqual(e["correct_option"], other)
        self.assertEqual(e["answer"], next(o["text"] for o in opts if o["id"] == other))
        for patch in ({"answer": "free text"}, {"correct_option": "z"}, {"options": opts[:1]}, {"options": opts + [{"id": "a", "text": "dup"}]}):
            with self.assertRaises(ValidationError):
                S.edit_item(ALICE, m["item_id"], patch)

    def test_edit_evidence_is_validated_against_stored_evidence(self):
        it = self.one(subtype="warning")
        mine = W.repo.list_evidence(W.M)
        foreign = W.repo.list_evidence(W.DB1)[0]
        for ev in ([], [{"evidence_id": foreign["id"]}], [{"evidence_id": mine[0]["id"], "quote": "not in this evidence"}], [{"evidence_id": "x"}]):
            with self.assertRaises(ValidationError):
                S.edit_item(ALICE, it["item_id"], {"evidence": ev})
        ok = next(e for e in mine if e["source_type"] == "transcript")
        e = S.edit_item(ALICE, it["item_id"], {"evidence": [{"evidence_id": ok["id"], "quote": ok["text"].split(".")[0]}]})
        self.assertEqual([x["evidence_id"] for x in e["evidence"]], [ok["id"]])

    def test_delete_removes_item_and_regeneration_does_not_resurrect_it(self):
        it = self.one(subtype="cloze")
        self.assertTrue(S.delete_item(ALICE, it["item_id"])["deleted"])
        self.assertNotIn(it["item_id"], {i["item_id"] for i in items(W.M)})
        with self.assertRaises(NotFoundError):
            S.get_item(ALICE, it["item_id"])
        with self.assertRaises(NotFoundError):
            S.delete_item(ALICE, it["item_id"])
        r = S.generate_items(ALICE, W.M)
        self.assertEqual(r["created"], 0)
        self.assertNotIn(it["prompt"], {i["prompt"] for i in items(W.M)})

    def test_deleting_knowledge_removes_derived_items_and_sticks(self):
        k = next(x for x in kn(W.M, "formula") if x["title"] == "Chain rule")
        derived = [i for i in items(W.M) if i["knowledge_id"] == k["knowledge_id"]]
        self.assertEqual(len(derived), 1)
        S.delete_knowledge(ALICE, k["knowledge_id"])
        self.assertNotIn("Chain rule", {x["title"] for x in kn(W.M, "formula")})
        self.assertNotIn(derived[0]["item_id"], {i["item_id"] for i in items(W.M)})
        S.regenerate_knowledge(ALICE, W.M)
        self.assertNotIn("Chain rule", {x["title"] for x in kn(W.M, "formula")})                       # tombstone holds
        with self.assertRaises(NotFoundError):
            S.delete_knowledge(ALICE, k["knowledge_id"])

    def test_regenerating_one_mcq_varies_distractors_and_keeps_validity(self):
        m = self.one("mcq", "mcq_definition", W.M)
        seen, txt = set(), all_text(W.M)
        cur = S.get_item(ALICE, m["item_id"])
        seen.add(tuple(o["text"] for o in cur["options"]))
        for n in range(1, 6):
            cur = S.regenerate_item(ALICE, m["item_id"])
            self.assertEqual((cur["item_id"], cur["regen_count"], cur["origin"]), (m["item_id"], n, "generated"))
            self.assertEqual(len(cur["options"]), 4)
            for o in cur["options"]:
                self.assertIn(norm(o["text"]), txt)
            self.assertEqual(sum(e["role"] == "distractor" for e in cur["evidence"]), 3)
            seen.add(tuple(o["text"] for o in cur["options"]))
        self.assertGreater(len(seen), 1)

    def test_regenerating_an_edited_item_restores_the_generated_version(self):
        it = self.one(subtype="formula")
        S.edit_item(ALICE, it["item_id"], {"prompt": "custom prompt"})
        r = S.regenerate_item(ALICE, it["item_id"])
        self.assertEqual((r["origin"], r["verified"]), ("generated", True))
        self.assertNotEqual(r["prompt"], "custom prompt")

    def test_regenerate_fails_cleanly_when_its_source_is_gone(self):
        it = next(i for i in items(W.M) if i["subtype"] == "exam")
        S.delete_knowledge(ALICE, it["knowledge_id"])                                                  # edited items would survive; generated ones go
        with self.assertRaises(NotFoundError):
            S.regenerate_item(ALICE, it["item_id"])

    def test_edited_item_detached_when_its_knowledge_is_deleted(self):
        it = self.one(subtype="fact_cloze")
        S.edit_item(ALICE, it["item_id"], {"difficulty": "hard"})
        S.delete_knowledge(ALICE, it["knowledge_id"])
        kept = S.get_item(ALICE, it["item_id"])
        self.assertIsNone(kept["knowledge_id"])
        with self.assertRaises(ConflictError) as c:
            S.regenerate_item(ALICE, it["item_id"])
        self.assertEqual(c.exception.code, "source_missing")

    def test_lecture_regeneration_preserves_ids_and_progress_of_unchanged_items(self):
        before = {i["item_id"] for i in items(W.P)}
        sid = S.start_session(ALICE, "lecture", W.P, kinds=["flashcard"], limit=2)
        for it in sid["items"]:
            S.attempt(ALICE, sid["session_id"], it["item_id"], self_grade="know")
        S.generate_items(ALICE, W.P)
        self.assertEqual(before, {i["item_id"] for i in items(W.P)})
        self.assertEqual(S.progress(ALICE, "lecture", W.P)["attempted"], 2)

    def test_changed_evidence_marks_stale_then_regeneration_replaces_generated_items_only(self):
        lid = fresh_sparse("Stale")
        S.list_items(ALICE, lid)
        keep = next(i for i in items(lid) if i["subtype"] == "definition")
        S.edit_item(ALICE, keep["item_id"], {"difficulty": "medium"})
        seg = next(s for s in W.repo.viewer_data(lid)["segments"] if s["text"].startswith("A matrix"))
        W.repo.update_segment_text(lid, seg["id"], "A matrix is defined as a rectangular grid of numbers arranged in rows and columns.")
        self.assertTrue(S.list_items(ALICE, lid)["stale"])
        self.assertTrue(S.knowledge(ALICE, lid)["stale"])
        S.regenerate_knowledge(ALICE, lid)
        r = S.generate_items(ALICE, lid)
        self.assertGreater(r["created"], 0)
        self.assertFalse(S.list_items(ALICE, lid)["stale"])
        self.assertIn("grid of numbers", " ".join(i["answer"] for i in items(lid)))
        self.assertNotIn("rectangular array", " ".join(i["answer"] for i in items(lid) if i["origin"] == "generated"))
        self.assertEqual(S.get_item(ALICE, keep["item_id"])["origin"], "edited")

    def test_reprocessing_leaves_flagged_snapshots_not_dangling_claims(self):
        from core.lecture.align import Segment
        from core.lecture.testkit import segments
        lid = fresh_empty("Reprocessed")
        S.knowledge(ALICE, lid)
        W.env.process(lid, segments(Segment(0.5, 3.0, "A tensor is defined as a multidimensional array of numbers."),
                                    Segment(3.0, 5.0, "A scalar is defined as a single number with no direction.")))
        S.regenerate_knowledge(ALICE, lid)
        S.generate_items(ALICE, lid)
        it = items(lid)[0]
        old = it["item_id"]
        S.edit_item(ALICE, old, {"difficulty": "hard"})
        W.env.process(lid, segments(Segment(0.5, 3.0, "A vector is defined as an ordered list of numbers.")))          # evidence ids all change
        e = S.get_item(ALICE, old)
        self.assertTrue(e["stale"])
        self.assertFalse(e["evidence"][0]["available"])
        self.assertIn("array of numbers", e["evidence"][0]["quote"] + e["answer"] + e["prompt"])              # quote snapshot still readable
        self.assertTrue(S.list_items(ALICE, lid)["stale"])
        S.regenerate_knowledge(ALICE, lid)
        S.generate_items(ALICE, lid)
        fresh = [i for i in items(lid) if i["origin"] == "generated"]
        self.assertTrue(fresh and all(e["available"] for i in fresh for e in i["evidence"]))
        self.assertNotIn("array of numbers", " ".join(i["answer"] for i in fresh))


# ───────────────────────── sessions + progress ─────────────────────────
class TestStudySessionsAndProgress(unittest.TestCase):
    def start(self, user=ALICE, lid=None, **kw):
        return S.start_session(user, "lecture", lid or W.P, **kw)

    def test_session_hides_answers_until_attempted(self):
        s = self.start(kinds=["mcq", "flashcard"], limit=6)
        self.assertEqual((s["summary"]["total"], s["summary"]["answered"], s["summary"]["complete"]), (6, 0, False))
        for i in s["items"]:
            self.assertNotIn("answer", i)
            self.assertNotIn("reveal", i)
            self.assertNotIn("correct_option", i)
            self.assertFalse(i["attempted"])
        self.assertNotIn("Because", str(s))

    def test_mcq_attempt_is_graded_server_side_with_reveal(self):
        s = self.start(kinds=["mcq"], limit=3)
        it = s["items"][0]
        truth = S.get_item(ALICE, it["item_id"])
        wrong = next(o["id"] for o in it["options"] if o["id"] != truth["correct_option"])
        r = S.attempt(ALICE, s["session_id"], it["item_id"], selected_option=wrong)
        self.assertEqual((r["correct"], r["selected_option"]), (False, wrong))
        self.assertEqual(r["reveal"]["correct_option"], truth["correct_option"])
        self.assertTrue(r["reveal"]["explanation"].startswith("The lecture says: “"))
        self.assertTrue(all(e["available"] and e["citation"]["label"] for e in r["reveal"]["evidence"]))
        self.assertEqual(r["item_progress"], {"attempts": 1, "streak": 0, "mastered": False})
        it2 = s["items"][1]
        t2 = S.get_item(ALICE, it2["item_id"])
        r2 = S.attempt(ALICE, s["session_id"], it2["item_id"], selected_option=t2["correct_option"])
        self.assertTrue(r2["correct"])
        self.assertEqual((r2["summary"]["answered"], r2["summary"]["correct"], r2["summary"]["accuracy"]), (2, 1, 0.5))

    def test_flashcards_and_short_answers_are_self_graded(self):
        s = self.start(kinds=["flashcard", "short_answer"], limit=4)
        a, b, c = s["items"][:3]
        self.assertTrue(S.attempt(ALICE, s["session_id"], a["item_id"], self_grade="know")["correct"])
        self.assertFalse(S.attempt(ALICE, s["session_id"], b["item_id"], self_grade="unsure")["correct"])
        self.assertFalse(S.attempt(ALICE, s["session_id"], c["item_id"], self_grade="dont_know")["correct"])

    def test_short_answer_feedback_lists_key_points_hit(self):
        s = self.start(kinds=["short_answer"], limit=1)
        it = s["items"][0]
        truth = S.get_item(ALICE, it["item_id"])
        text = f"It is about {truth['key_points'][0]} and something else"
        r = S.attempt(ALICE, s["session_id"], it["item_id"], self_grade="know", response_text=text)
        self.assertEqual(r["reveal"]["key_points_hit"], [truth["key_points"][0]])
        with self.assertRaises(ValidationError):
            S.attempt(ALICE, s["session_id"], it["item_id"], self_grade="know", response_text="x" * 2001)

    def test_attempt_validation_and_conflicts(self):
        s = self.start(kinds=["mcq", "flashcard"], limit=4)
        mcq = next(i for i in s["items"] if i["kind"] == "mcq")
        card = next(i for i in s["items"] if i["kind"] == "flashcard")
        for kw in ({}, {"selected_option": "zz"}, {"self_grade": "know"}):
            with self.assertRaises(ValidationError):
                S.attempt(ALICE, s["session_id"], mcq["item_id"], **kw)
        for kw in ({}, {"self_grade": "maybe"}, {"selected_option": "a"}):
            with self.assertRaises(ValidationError):
                S.attempt(ALICE, s["session_id"], card["item_id"], **kw)
        S.attempt(ALICE, s["session_id"], card["item_id"], self_grade="know")
        with self.assertRaises(ConflictError) as c:
            S.attempt(ALICE, s["session_id"], card["item_id"], self_grade="know")
        self.assertEqual(c.exception.code, "already_answered")
        outside = next(i["item_id"] for i in items(W.P) if i["item_id"] not in {x["item_id"] for x in s["items"]})
        with self.assertRaises(NotFoundError):
            S.attempt(ALICE, s["session_id"], outside, self_grade="know")
        with self.assertRaises(NotFoundError):
            S.attempt(ALICE, s["session_id"], 10 ** 7, self_grade="know")

    def test_session_state_completion_and_finish(self):
        s = self.start(kinds=["flashcard"], limit=3)
        for i in s["items"]:
            S.attempt(ALICE, s["session_id"], i["item_id"], self_grade="know")
        st = S.session(ALICE, s["session_id"])
        self.assertEqual((st["summary"]["answered"], st["summary"]["remaining"], st["summary"]["complete"], st["summary"]["accuracy"]), (3, 0, True, 1.0))
        self.assertTrue(all(i["attempted"] and "reveal" in i and i["reveal"]["answer"] for i in st["items"]))
        self.assertIsNone(st["finished_at"])
        f = S.finish(ALICE, s["session_id"])
        self.assertIsNotNone(f["finished_at"])
        self.assertEqual(S.finish(ALICE, s["session_id"])["finished_at"], f["finished_at"])             # idempotent

    def test_progress_numbers_are_exact(self):
        lid = fresh_sparse("ProgressExact")
        S.list_items(ALICE, lid)
        total = len(items(lid))
        before = S.progress(ALICE, "lecture", lid)
        s = S.start_session(ALICE, "lecture", lid, kinds=["flashcard"], limit=4)
        for n, i in enumerate(s["items"]):
            S.attempt(ALICE, s["session_id"], i["item_id"], self_grade="know" if n < 3 else "dont_know")
        p = S.progress(ALICE, "lecture", lid)
        self.assertEqual((p["total"], p["attempted"] - before["attempted"], p["total_attempts"] - before["total_attempts"]), (total, 4, 4))
        self.assertEqual(p["completion_pct"], round(100 * p["attempted"] / total))
        self.assertEqual(p["by_kind"]["flashcard"]["attempted"], p["attempted"])
        self.assertEqual(p["by_kind"]["mcq"]["total"], 0)
        self.assertIsNone(p["by_lecture"])
        self.assertGreaterEqual(p["sessions"]["total"], 1)

    def test_mastery_needs_consecutive_correct_answers_and_resets_on_a_miss(self):
        lid = W.P
        item = next(i for i in S.list_items(ALICE, lid, kind="flashcard")["items"] if i["subtype"] == "fact")
        iid = item["item_id"]

        def answer(grade, review=False):
            sess = S.start_session(ALICE, "lecture", lid, kinds=["flashcard"], limit=50, include_mastered=review)
            return S.attempt(ALICE, sess["session_id"], iid, self_grade=grade)["item_progress"]
        self.assertEqual(answer("know"), {"attempts": 1, "streak": 1, "mastered": False})
        self.assertEqual(answer("know"), {"attempts": 2, "streak": 2, "mastered": True})
        normal = S.start_session(ALICE, "lecture", lid, kinds=["flashcard"], limit=50)                  # mastered items leave normal sessions...
        self.assertNotIn(iid, {i["item_id"] for i in normal["items"]})
        self.assertEqual(answer("dont_know", True), {"attempts": 3, "streak": 0, "mastered": False})    # ...unless the learner asks to review them
        self.assertEqual(answer("know")["streak"], 1)
        self.assertGreaterEqual(S.progress(ALICE, "lecture", lid)["mastered"], 0)

    def test_sessions_prefer_unseen_then_weakest_items(self):
        lid = fresh_sparse("PreferUnseen")
        first = S.start_session(ALICE, "lecture", lid, kinds=["flashcard"], limit=2)
        for i in first["items"]:
            S.attempt(ALICE, S.session(ALICE, first["session_id"])["session_id"], i["item_id"], self_grade="know")
        second = S.start_session(ALICE, "lecture", lid, kinds=["flashcard"], limit=2)
        self.assertFalse({i["item_id"] for i in first["items"]} & {i["item_id"] for i in second["items"]})

    def test_deleted_item_leaves_active_sessions(self):
        s = self.start(kinds=["flashcard"], limit=3)
        gone = s["items"][0]["item_id"]
        lid = W.P
        S.delete_item(ALICE, gone)
        st = S.session(ALICE, s["session_id"])
        self.assertNotIn(gone, {i["item_id"] for i in st["items"]})
        self.assertEqual(st["summary"]["total"], 2)
        with self.assertRaises(NotFoundError):
            S.attempt(ALICE, s["session_id"], gone, self_grade="know")
        self.assertNotIn(gone, {i["item_id"] for i in items(lid)})

    def test_session_parameters_are_validated(self):
        for kw in ({"kinds": []}, {"kinds": ["essay"]}, {"kinds": "mcq"}, {"difficulty": "x"}, {"limit": 0}, {"limit": 51}, {"limit": "3"}, {"limit": True}):
            with self.assertRaises(ValidationError, msg=str(kw)):
                self.start(**kw)
        with self.assertRaises(ValidationError):
            S.start_session(ALICE, "planet", 1)

    def test_concept_review_tracking(self):
        lid = W.P
        concepts = S.knowledge(ALICE, lid, "concept")["items"]
        before = S.progress(ALICE, "lecture", lid)["concepts"]
        self.assertEqual(before["total"], len(concepts))
        r = S.review_concept(ALICE, concepts[0]["knowledge_id"])
        self.assertEqual(r["count"], 1)
        self.assertEqual(S.review_concept(ALICE, concepts[0]["knowledge_id"])["count"], 2)
        after = S.progress(ALICE, "lecture", lid)["concepts"]
        self.assertEqual((after["reviewed"] - before["reviewed"], after["total"]), (1, len(concepts)))
        self.assertEqual(S.knowledge(ALICE, lid, "concept")["items"][0]["reviewed"]["count"], 2)


# ───────────────────────── course memory ─────────────────────────
class TestCourseMemory(unittest.TestCase):
    def test_overview_lists_only_processed_lectures_of_this_course(self):
        ov = C.overview(ALICE, W.c_dl)
        self.assertEqual([l["lecture_id"] for l in ov["lectures"]], [W.DL1, W.DL2])
        self.assertEqual(ov["name"], "Deep Learning")
        self.assertNotIn(W.queued, [l["lecture_id"] for l in ov["lectures"]])               # unprocessed lecture ignored
        self.assertEqual([l["lecture_id"] for l in C.overview(ALICE, W.c_db)["lectures"]], [W.DB1])

    def test_knowledge_spans_the_courses_lectures_and_nothing_else(self):
        k = C.knowledge(ALICE, W.c_dl)
        self.assertEqual({i["lecture_id"] for i in k["items"]}, {W.DL1, W.DL2})
        self.assertEqual({i["lecture_title"] for i in k["items"]}, {"DL1", "DL2"})
        text = " ".join((i["title"] + " " + (i["body"] or "")).lower() for i in k["items"])
        self.assertNotIn("primary key", text)
        self.assertIn("momentum", text)
        self.assertIn("backpropagation", text)
        self.assertEqual({i["lecture_id"] for i in C.knowledge(ALICE, W.c_dl, lecture_id=W.DL2)["items"]}, {W.DL2})
        self.assertEqual({i["kind"] for i in C.knowledge(ALICE, W.c_dl, kind="warning")["items"]}, {"warning"})
        with self.assertRaises(NotFoundError):
            C.knowledge(ALICE, W.c_dl, lecture_id=W.DB1)                                   # a lecture of another course isn't addressable
        with self.assertRaises(ValidationError):
            C.knowledge(ALICE, W.c_dl, kind="bogus")

    def test_concepts_merge_across_lectures_with_evidence_from_each(self):
        cs = {c["title"]: c for c in C.concepts(ALICE, W.c_dl)["concepts"]}
        lr = cs["Learning rate"]
        self.assertEqual(lr["lectures_count"], 2)
        self.assertEqual({l["lecture_id"] for l in lr["lectures"]}, {W.DL1, W.DL2})
        self.assertEqual({d["lecture_id"] for d in lr["definitions"]}, {W.DL1, W.DL2})
        bodies = {d["body"] for d in lr["definitions"]}
        self.assertEqual(bodies, {F.DL1[1].text, F.DL2[0].text})
        for d in lr["definitions"]:
            self.assertTrue(d["evidence"][0]["available"])
            self.assertEqual(d["evidence"][0]["lecture_id"], d["lecture_id"])
        self.assertEqual([c["title"] for c in C.concepts(ALICE, W.c_dl, shared_only=True)["concepts"]], ["Learning rate"])
        self.assertEqual(next(iter(C.concepts(ALICE, W.c_dl)["concepts"]))["title"], "Learning rate")      # most-shared first

    def test_search_finds_course_knowledge_and_evidence_with_citations(self):
        r = C.search(ALICE, W.c_dl, "momentum")
        self.assertTrue(r["knowledge"] and r["evidence"])
        self.assertEqual({k["lecture_id"] for k in r["knowledge"]} | {e["lecture_id"] for e in r["evidence"]}, {W.DL2})
        e = r["evidence"][0]
        self.assertTrue(e["citation"]["label"] and e["citation"]["seek_seconds"] is not None)
        self.assertEqual(e["lecture_title"], "DL2")
        r = C.search(ALICE, W.c_dl, "learning rate")
        self.assertEqual({e["lecture_id"] for e in r["evidence"]}, {W.DL1, W.DL2})
        self.assertEqual({l["lecture_id"] for l in r["lectures_searched"]}, {W.DL1, W.DL2})
        for bad in ("", "  ", "x" * 201):
            with self.assertRaises(ValidationError):
                C.search(ALICE, W.c_dl, bad)

    def test_courses_do_not_mix_even_for_the_same_owner(self):
        self.assertEqual((C.search(ALICE, W.c_dl, "primary key")["knowledge"], C.search(ALICE, W.c_dl, "primary key")["evidence"]), ([], []))
        self.assertTrue(C.search(ALICE, W.c_db, "primary key")["knowledge"])
        self.assertEqual(C.search(ALICE, W.c_db, "momentum")["evidence"], [])
        db_items = C.study_items(ALICE, W.c_db)["items"]
        self.assertEqual({i["lecture_id"] for i in db_items}, {W.DB1})
        dl_items = C.study_items(ALICE, W.c_dl)["items"]
        self.assertEqual({i["lecture_id"] for i in dl_items}, {W.DL1, W.DL2})
        self.assertNotIn("primary key", " ".join(i["prompt"].lower() for i in dl_items))
        self.assertNotIn("momentum", " ".join(i["prompt"].lower() for i in db_items))

    def test_course_study_session_and_progress_by_lecture(self):
        s = S.start_session(ALICE, "course", W.c_dl, kinds=["mcq"], limit=20)
        self.assertEqual(len(s["items"]), 20)
        self.assertEqual({i["lecture_id"] for i in s["items"]}, {W.DL1, W.DL2})                  # a course session draws from every lecture of the course
        it = s["items"][0]
        S.attempt(ALICE, s["session_id"], it["item_id"], selected_option=it["options"][0]["id"])
        p = S.progress(ALICE, "course", W.c_dl)
        self.assertEqual(set(p["by_lecture"]), {str(W.DL1), str(W.DL2)})
        self.assertEqual(p["attempted"], sum(b["attempted"] for b in p["by_lecture"].values()))
        self.assertEqual(p["scope"], {"type": "course", "id": W.c_dl})

    def test_unprocessed_and_rogue_lectures_never_enter_a_course(self):
        rogue = W.repo.create_lecture(BOB, "Rogue", None, W.c_dl, W.repo.ensure_personal_workspace(BOB))     # claims the course, wrong workspace
        W.repo.set_status(rogue, "done")
        _, ids = S.course_lectures(ALICE, W.c_dl)
        self.assertNotIn(rogue, ids)
        self.assertNotIn(W.queued, ids)
        self.assertEqual(ids, [W.DL1, W.DL2])
        self.assertNotIn(rogue, {l["lecture_id"] for l in C.overview(ALICE, W.c_dl)["lectures"]})


# ───────────────────────── isolation + authorization ─────────────────────────
class TestIsolationAndAuthorization(unittest.TestCase):
    def test_non_members_get_not_found_everywhere_identical_to_missing(self):
        item = items(W.DL1, reveal=False)[0]["item_id"]
        kid = kn(W.DL1)[0]["knowledge_id"]
        calls = [lambda u, l: S.knowledge(u, l), lambda u, l: S.concepts(u, l), lambda u, l: S.regenerate_knowledge(u, l),
                 lambda u, l: S.list_items(u, l), lambda u, l: S.generate_items(u, l), lambda u, l: S.start_session(u, "lecture", l),
                 lambda u, l: S.progress(u, "lecture", l)]
        for call in calls:
            for who in (BOB, EVE):
                with self.assertRaises(NotFoundError) as forbidden:
                    call(who, W.DL1)
                with self.assertRaises(NotFoundError) as missing:
                    call(who, 10 ** 7)
                self.assertEqual(str(forbidden.exception), str(missing.exception))
        for call in (lambda u: S.get_item(u, item), lambda u: S.edit_item(u, item, {"difficulty": "hard"}), lambda u: S.delete_item(u, item),
                     lambda u: S.regenerate_item(u, item), lambda u: S.delete_knowledge(u, kid), lambda u: S.review_concept(u, kid)):
            with self.assertRaises(NotFoundError):
                call(BOB)
        self.assertEqual(S.get_item(ALICE, item)["item_id"], item)                           # nothing was changed by the attempts

    def test_course_endpoints_deny_non_members(self):
        for call in (lambda u: C.overview(u, W.c_dl), lambda u: C.knowledge(u, W.c_dl), lambda u: C.concepts(u, W.c_dl),
                     lambda u: C.search(u, W.c_dl, "momentum"), lambda u: C.study_items(u, W.c_dl),
                     lambda u: S.start_session(u, "course", W.c_dl), lambda u: S.progress(u, "course", W.c_dl)):
            for who in (BOB, EVE):
                with self.assertRaises(NotFoundError):
                    call(who)
        with self.assertRaises(NotFoundError):
            C.overview(ALICE, 10 ** 7)                                                         # missing course == not yours

    def test_same_content_in_another_users_course_stays_separate(self):
        self.assertEqual(C.overview(BOB, W.c_bob)["lectures"][0]["lecture_id"], W.BOBDL)
        bob = C.knowledge(BOB, W.c_bob)["items"]
        self.assertEqual({i["lecture_id"] for i in bob}, {W.BOBDL})
        self.assertNotIn("momentum", " ".join(i["title"].lower() for i in bob))                # Alice's DL2 content never reaches Bob
        with self.assertRaises(NotFoundError):
            C.knowledge(ALICE, W.c_bob)
        with self.assertRaises(NotFoundError):
            S.get_item(ALICE, items_of(BOB, W.BOBDL)[0])

    def test_workspace_members_share_content_but_never_progress_or_sessions(self):
        self.assertEqual(C.overview(BOB, W.c_group)["lectures"][0]["lecture_id"], W.GROUP)       # Bob is a member of the group workspace
        with self.assertRaises(NotFoundError):
            C.overview(BOB, W.c_dl)                                                                # ...but not of Alice's personal one
        a = S.start_session(ALICE, "lecture", W.GROUP, kinds=["flashcard"], limit=3)
        b = S.start_session(BOB, "lecture", W.GROUP, kinds=["flashcard"], limit=3)
        S.attempt(ALICE, a["session_id"], a["items"][0]["item_id"], self_grade="know")
        self.assertEqual(S.progress(BOB, "lecture", W.GROUP)["attempted"], 0)
        self.assertEqual(S.progress(ALICE, "lecture", W.GROUP)["attempted"], 1)
        with self.assertRaises(NotFoundError):
            S.session(BOB, a["session_id"])
        with self.assertRaises(NotFoundError):
            S.attempt(BOB, a["session_id"], a["items"][0]["item_id"], self_grade="know")
        with self.assertRaises(NotFoundError):
            S.finish(BOB, a["session_id"])
        with self.assertRaises(NotFoundError):
            S.session(ALICE, b["session_id"])
        S.attempt(BOB, b["session_id"], b["items"][0]["item_id"], self_grade="dont_know")
        self.assertEqual(S.progress(ALICE, "lecture", W.GROUP)["total_attempts"], 1)
        S.review_concept(BOB, kn_id(W.GROUP, "concept"))
        self.assertEqual(S.progress(ALICE, "lecture", W.GROUP)["concepts"]["reviewed"], 0)

    def test_session_of_a_course_is_invalid_for_a_user_who_cannot_access_it(self):
        s = S.start_session(BOB, "course", W.c_group, limit=2)
        self.assertEqual(len(s["items"]), 2)
        with self.assertRaises(NotFoundError):
            S.session(EVE, s["session_id"])

    def test_deleting_a_lecture_removes_its_study_data(self):
        lid = W.env.lecture(ALICE, W.video, None, title="Doomed", course_id=W.c_scratch)
        from core.lecture.align import Segment
        from core.lecture.testkit import segments
        W.env.process(lid, segments(Segment(0.5, 3.0, "A vector is defined as an ordered list of numbers."), Segment(3.0, 6.0, "A matrix is defined as a rectangular array of numbers.")))
        S.list_items(ALICE, lid)
        s = S.start_session(ALICE, "lecture", lid, limit=2)
        S.attempt(ALICE, s["session_id"], s["items"][0]["item_id"], self_grade="know")
        S.review_concept(ALICE, S.knowledge(ALICE, lid, "concept")["items"][0]["knowledge_id"])
        W.env.assets.delete_lecture(ALICE, lid)
        c = store.conn()
        for table in ("knowledge_items", "study_items", "study_meta", "knowledge_meta", "knowledge_tombstones"):
            self.assertEqual(c.execute(f"SELECT COUNT(*) FROM {table} WHERE lecture_id=?", (lid,)).fetchone()[0], 0, table)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM study_sessions WHERE scope_type='lecture' AND scope_id=?", (lid,)).fetchone()[0], 0)
        for orphan in ("SELECT COUNT(*) FROM study_item_evidence WHERE item_id NOT IN (SELECT id FROM study_items)",
                       "SELECT COUNT(*) FROM knowledge_evidence WHERE item_id NOT IN (SELECT id FROM knowledge_items)",
                       "SELECT COUNT(*) FROM study_progress WHERE item_id NOT IN (SELECT id FROM study_items)",
                       "SELECT COUNT(*) FROM study_attempts WHERE item_id NOT IN (SELECT id FROM study_items)",
                       "SELECT COUNT(*) FROM knowledge_reviews WHERE knowledge_id NOT IN (SELECT id FROM knowledge_items)"):
            self.assertEqual(c.execute(orphan).fetchone()[0], 0, orphan)

    def test_responses_never_expose_storage_paths_keys_or_other_users_data(self):
        import json
        s = S.start_session(ALICE, "lecture", W.DL1, limit=3)
        blob = json.dumps([S.knowledge(ALICE, W.DL1), S.concepts(ALICE, W.DL1), s, S.get_item(ALICE, s["items"][0]["item_id"]),
                           C.concepts(ALICE, W.c_dl), C.search(ALICE, W.c_dl, "learning rate")])
        for leak in (W.tmp, "storage_key", "objects/", "signing"):
            self.assertNotIn(leak, blob)


def items_of(user, lid):
    return [i["item_id"] for i in StudyService(W.repo, W.storage).list_items(user, lid)["items"]]


def kn_id(lid, kind):
    return S.knowledge(ALICE, lid, kind)["items"][0]["knowledge_id"]


if __name__ == "__main__":
    unittest.main()
