"""AI practice quiz rules: only questions citing this lecture's real evidence survive; goal/count limits hold."""
import json
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import server.api.routes_ai_quiz as R
from core.lecture import llm
from server.api.deps import get_current_user

EVIDENCE = [{"id": 11, "lecture_id": 1, "source_type": "transcript", "slide_index": 1, "start": 11.0, "text": "If the learning rate is too large training can diverge."},
            {"id": 12, "lecture_id": 1, "source_type": "slide", "slide_index": 2, "start": None, "text": "Regularization adds a penalty on large weights."}]


class FakeRepo:
    def list_evidence(self, lecture_id): return EVIDENCE


def q(ids, opts=4, ci=1):
    return {"question": "Q?", "options": [f"o{i}" for i in range(opts)], "correct_index": ci, "explanation": "e", "evidence_ids": ids}


class TestAiQuiz(unittest.TestCase):
    def setUp(self):
        app = FastAPI(); app.include_router(R.router)
        app.dependency_overrides[get_current_user] = lambda: {"id": 7}
        self.c = TestClient(app)
        self.patches = [mock.patch.object(R.config, "get_repo", return_value=FakeRepo()),
                        mock.patch.object(R.authz, "get_lecture", return_value={"id": 1, "status": "done", "title": "GD"}),
                        mock.patch.object(R.quotas, "check_ask", return_value=None)]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in self.patches: p.stop()

    def post(self, model_out, **body):
        done = llm.Completion(json.dumps(model_out), "openai", "gpt-6-astra")
        with mock.patch.object(R, "_call_model", return_value=done):
            return self.c.post("/lectures/1/ai-quiz", json={"goal": "learning rate", "count": 5, **body})

    def test_questions_with_invented_evidence_ids_are_dropped(self):
        r = self.post({"questions": [q([11]), q([999]), q([12, 999])]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["questions"]), 2)
        self.assertEqual(r.json()["dropped"], 1)
        self.assertEqual([c["evidence_id"] for c in r.json()["questions"][1]["citations"]], [12])

    def test_question_needs_exactly_four_options_and_valid_answer(self):
        r = self.post({"questions": [q([11], opts=3), q([11], ci=4), q([11])]})
        self.assertEqual(len(r.json()["questions"]), 1)

    def test_no_grounded_question_is_an_error_not_an_empty_quiz(self):
        self.assertEqual(self.post({"questions": [q([999])]}).status_code, 422)

    def test_count_must_be_3_to_10(self):
        self.assertEqual(self.post({"questions": []}, count=2).status_code, 422)
        self.assertEqual(self.post({"questions": []}, count=11).status_code, 422)

    def test_response_names_the_provider_that_wrote_it(self):
        r = self.post({"questions": [q([11])]})
        self.assertEqual((r.json()["provider"], r.json()["model"]), ("openai", "gpt-6-astra"))

    def chain_post(self, chain):
        with mock.patch.object(R.config, "get_llm_chain", return_value=chain):
            return self.c.post("/lectures/1/ai-quiz", json={"goal": "learning rate", "count": 5})

    def test_no_provider_configured_is_503(self):
        r = self.chain_post(llm.LLMChain([]))
        self.assertEqual(r.status_code, 503)
        self.assertIn("OPENAI_API_KEY", r.json()["detail"])

    def test_provider_failures_map_to_clear_statuses(self):
        class P(llm.Provider):
            def __init__(self, name, reason):
                super().__init__(name); self.name, self.reason = name, reason
            def configured(self): return True
            def installed(self): return True
            def complete(self, *a): raise llm.LLMError(self.reason)
        for reasons, status in [(["refusal", "refusal"], 403), (["rate_limited", "rate_limited"], 429),
                                (["timeout", "rate_limited"], 502), (["auth"], 503), (["quota_exhausted"], 503),
                                (["quota_exhausted", "unsupported_model"], 503)]:
            chain = llm.LLMChain([P(f"p{i}", r) for i, r in enumerate(reasons)], cooldown=0)
            self.assertEqual(self.chain_post(chain).status_code, status, reasons)

    def test_no_credits_is_reported_as_no_credits_not_busy(self):
        """Regression: OpenAI answers HTTP 429 insufficient_quota when the account has no credits; the quiz used to say
        "The quiz writer is busy. Try again in a minute." even though waiting cannot help."""
        class P(llm.Provider):
            name = "openai"
            def configured(self): return True
            def installed(self): return True
            def complete(self, *a): raise llm.LLMError("quota_exhausted")
        r = self.chain_post(llm.LLMChain([P("gpt-6-astra")], cooldown=0))
        self.assertEqual(r.status_code, 503)
        self.assertIn("no API credits", r.json()["detail"])
        self.assertNotIn("busy", json.dumps(r.json()))

    def test_schema_is_sent_and_fallback_answer_is_used(self):
        class P(llm.Provider):
            def __init__(self, name, out=None):
                super().__init__(name + "-m"); self.name, self.out, self.seen = name, out, None
            def configured(self): return True
            def installed(self): return True
            def complete(self, system, user, schema, schema_name):
                self.seen, self.user = (schema, schema_name), user
                if self.out is None: raise llm.LLMError("timeout")
                return self.out
        a, b = P("openai"), P("anthropic", json.dumps({"questions": [q([11])]}))
        r = self.chain_post(llm.LLMChain([a, b], cooldown=0))
        self.assertEqual((r.status_code, r.json()["provider"]), (200, "anthropic"))
        self.assertEqual(b.seen, (R.SCHEMA, "practice_quiz"))
        self.assertIn("[E11]", b.user)                                       # the lecture evidence is what the model sees
        self.assertIn("learning rate", b.user)

    def test_unprocessed_lecture_is_409(self):
        with mock.patch.object(R.authz, "get_lecture", return_value={"id": 1, "status": "processing"}):
            self.assertEqual(self.post({"questions": [q([11])]}).status_code, 409)


if __name__ == "__main__":
    unittest.main()
