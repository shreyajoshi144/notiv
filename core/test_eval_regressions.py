"""Regressions found by the evaluation tools (tools/eval_ai_study.py, tools/eval_format_matrix.py)."""
import unittest

from core.lecture.ask import extractive_answer


class TestQuoteAnswerSkipsHeadings(unittest.TestCase):
    def test_heading_is_not_quoted_as_the_answer(self):
        """eval_ai_study: "What does the CAP theorem say?" was answered with the slide title "CAP theorem" — it matches the
        question words best but answers nothing."""
        chunk = {"id": 7, "source_type": "slide", "context": False,
                 "text": "CAP theorem\nA distributed system can provide at most two of consistency, availability and partition tolerance."}
        a = extractive_answer(["cap", "theorem"], [chunk])
        self.assertIn("at most two", a["answer"])
        self.assertEqual(a["cited"], [7])

    def test_a_heading_alone_is_still_quoted(self):
        a = extractive_answer(["agenda"], [{"id": 3, "source_type": "slide", "context": False, "text": "Agenda"}])
        self.assertIn("Agenda", a["answer"])


if __name__ == "__main__":
    unittest.main()
