"""Hard-wrapped PDF text (Step 4): lines broken at the page margin are re-joined before they become evidence, and written
prose on slides / document pages feeds definitions, facts and study items exactly like speech does."""
import os
import shutil
import tempfile
import unittest

from core.lecture import knowledge, slides
from core.lecture.ask import AskService
from core.lecture.make_test_lecture import make_text_pdf
from core.lecture.study import StudyService
from core.lecture.testkit import Env

# What pdftotext really returns for a document page: prose broken at the margin.
WRAPPED = ("Cell Biology Lecture Notes\n"
           "Photosynthesis is defined as the process by which green plants and some other\n"
           "organisms use sunlight to synthesize foods from carbon dioxide and water.\n"
           "Cellular respiration is the process by which cells break down glucose to release\n"
           "energy in the form of ATP. Respiration produces about 36 ATP molecules per glucose\n"
           "molecule because oxidative phosphorylation is highly efficient.")


class TestReflow(unittest.TestCase):
    def test_wrapped_prose_is_rejoined_and_the_title_kept(self):
        out = slides.reflow(WRAPPED).split("\n")
        self.assertEqual(out[0], "Cell Biology Lecture Notes")
        self.assertEqual(out[1], "Photosynthesis is defined as the process by which green plants and some other organisms "
                                 "use sunlight to synthesize foods from carbon dioxide and water.")
        self.assertTrue(out[2].startswith("Cellular respiration is the process") and out[2].endswith("highly efficient."))
        self.assertEqual(len(out), 3)

    def test_slide_text_is_returned_byte_for_byte(self):
        for t in ("Gradient Descent\nUpdate rule: w = w - lr * gradient\nLoss: L = (1/n) * sum((y - y_hat)^2)",
                  "Agenda\n- Intro\n- Methods\n- Results", "  Indented title\n\nsecond", ""):
            self.assertEqual(slides.reflow(t), t)

    def test_bullets_and_finished_sentences_stay_separate(self):
        t = ("Gradient descent repeatedly moves the weights in the direction that lowers the loss.\n"
             "Momentum keeps a running average of the past gradients to smooth the update steps\n"
             "- learning rate controls how large every single update step is in practice\n"
             "1) choose a small learning rate before you try any of the more complicated schedules")
        self.assertEqual(slides.reflow(t).split("\n"), t.split("\n"))

    def test_lowercase_continuation_joins_even_on_narrow_pages(self):
        # OCR of a large-font scan: short lines, still one sentence
        t = "Photosynthesis is defined as the process\nby which plants convert light into energy."
        self.assertEqual(slides.reflow(t), "Photosynthesis is defined as the process by which plants convert light into energy.")
        self.assertEqual(slides.reflow("Gradient Descent\nuses the slope"), "Gradient Descent\nuses the slope")   # a title stays

    def test_broken_hyphenated_word_keeps_its_hyphen(self):
        t = "Models that are trained with this objective become strongly self-\naware of their own uncertainty estimates."
        self.assertIn("self-aware", slides.reflow(t))

    def test_paragraph_breaks_survive(self):
        t = ("This first paragraph is long enough to look like prose from a document page\n"
             "and it ends here.\n\nA second paragraph starts after the blank line and keeps going on.")
        self.assertEqual(slides.reflow(t).split("\n")[1], "")


class TestProse(unittest.TestCase):
    def test_is_prose(self):
        self.assertTrue(knowledge._is_prose("Photosynthesis is defined as the process by which plants make food."))
        for t in ("Gradient Descent", "Update rule: w = w - lr * gradient", "Adam combines momentum and adaptive learning rates",
                  "Short sentence here."):
            self.assertFalse(knowledge._is_prose(t), t)


class TestDocumentPdfEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = Env(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def doc(self, pages, name="doc.pdf"):
        lid = self.env.lecture(deck=make_text_pdf(os.path.join(self.tmp, "fx"), n=len(pages), slide_lines=pages, name=name))
        self.env.process(lid)
        return lid

    def test_document_pdf_yields_whole_sentence_evidence_definitions_and_flashcards(self):
        # a plain "X is the process ..." counts as a definition only when X is a title or recurs (same rule as speech)
        lid = self.doc([WRAPPED.split("\n") + ["Most cellular respiration in animal cells happens inside the mitochondria."]])
        [ev] = self.env.repo.list_evidence(lid)
        self.assertIn("other organisms use sunlight", ev["text"])                    # the wrap is gone from the evidence
        st = StudyService(self.env.repo, self.env.storage)
        g = st.generate_items(1, lid, ["flashcard"])
        self.assertEqual(g["status"], "ok")
        items = st.list_items(1, lid, kind="flashcard", reveal=True)["items"]
        prompts = {i["prompt"] for i in items}
        self.assertIn("What is Photosynthesis?", prompts)
        self.assertIn("What is Cellular respiration?", prompts)
        self.assertTrue(any(i.get("subtype") == "fact_cloze" and i["answer"] == "36" for i in items))
        for i in items:                                                             # verbatim: every answer is in the page
            if i.get("subtype") == "definition":
                self.assertIn(i["answer"], ev["text"])
        self.assertFalse(any("professor" in p.lower() for p in prompts))           # written source, not speech

    def test_ask_quotes_a_whole_sentence(self):
        lid = self.doc([WRAPPED.split("\n")])
        a = AskService(self.env.repo, self.env.storage, llm=None)._answer(lid, "What is photosynthesis?", [])
        self.assertTrue(a["grounded"])
        self.assertIn("carbon dioxide and water.", a["answer"])

    def test_page_opening_with_a_paragraph_is_not_a_concept_title(self):
        para = ["Photosynthesis is defined as the process by which green plants turn sunlight into food.",
                "Chlorophyll is the pigment that absorbs most of the light used during this process."]
        lid = self.doc([para])
        titles = [k["title"] for k in self.env.repo.list_knowledge([lid]) if k["kind"] == "concept"]
        self.assertFalse(any(len(t.split()) > 6 for t in titles), titles)

    def test_a_page_repeated_verbatim_does_not_break_generation(self):
        lid = self.doc([WRAPPED.split("\n"), WRAPPED.split("\n")])                  # same text on two pages
        g = StudyService(self.env.repo, self.env.storage).generate_items(1, lid, ["flashcard"])
        self.assertEqual(g["status"], "ok")
        defs = [k for k in self.env.repo.list_knowledge([lid]) if k["kind"] == "definition"]
        self.assertEqual(len({k["title"] for k in defs}), len(defs))

    def test_bullet_slides_are_unchanged_by_prose_mining(self):
        lid = self.doc([["Gradient Descent", "Update rule: w = w - lr * gradient", "Loss: L = (1/n) * sum((y - y_hat)^2)"]])
        kinds = sorted(k["kind"] for k in self.env.repo.list_knowledge([lid]))
        self.assertEqual(kinds, ["concept", "formula", "formula"])


if __name__ == "__main__":
    unittest.main()
