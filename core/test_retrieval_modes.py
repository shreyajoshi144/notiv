"""Retrieval honesty + provider failure handling. Real retrieval/ask code over a real SQLite store.
The "neural" embedders below are STAND-INS that exercise OUR plumbing (kind reporting, degradation, cool-down); they are not
a verification of Mistral or any real provider (UNVERIFIED live)."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

import numpy as np

from core.lecture import ask, health, retrieval, settings
from core.lecture.retrieval import EmbeddingUnavailable, SubwordHashEmbedder
from core.lecture.testkit import Env

TEXTS = ["Gradient descent minimises the loss function.", "Backpropagation computes gradients layer by layer.",
         "The professor mentioned the exam date.", "Regularisation reduces overfitting in neural networks."]


class NeuralStandIn(SubwordHashEmbedder):
    name = "neural-standin"
    neural = True
    calls = 0

    def embed(self, texts):
        type(self).calls += 1
        return super().embed(texts)


class Failing(NeuralStandIn):
    name = "failing-standin"

    def embed(self, texts):
        type(self).calls += 1
        raise EmbeddingUnavailable("TimeoutError")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = Env(self.tmp)
        self.repo = self.env.repo
        self.lid = self.env.lectures.create(1, "L")
        self.repo.replace_evidence(self.lid, [{"source_type": "transcript", "source_id": i, "text": t, "start": float(i * 5), "end": float(i * 5 + 4)}
                                              for i, t in enumerate(TEXTS, 1)])
        self.repo.rebuild_index(self.lid)
        retrieval.reset_breaker(); NeuralStandIn.calls = 0; Failing.calls = 0
        self._env = dict(os.environ)

    def tearDown(self):
        os.environ.clear(); os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)
        retrieval.reset_breaker()

    def r(self, emb, q="what is gradient descent"):
        return retrieval.retrieve(self.repo, self.lid, q, None, emb)


class TestKinds(Base):
    def test_default_embedder_is_reported_as_NOT_semantic(self):
        r = self.r(None)
        self.assertEqual((r["kind"], r["semantic"], r["mode"], r["degraded"]), ("lexical_subword", False, "hybrid", False))
        self.assertEqual(r["embedder"], "subword-hash-v1")                        # 'hybrid' mode does not imply semantic
        self.assertTrue(r["chunks"])

    def test_neural_embedder_is_reported_as_semantic(self):
        r = self.r(NeuralStandIn())
        self.assertEqual((r["kind"], r["semantic"], r["degraded"]), ("semantic", True, False))

    def test_ask_and_retrieve_responses_carry_the_kind(self):
        svc = ask.AskService(self.repo, self.env.storage, None, None)
        meta = svc._answer(self.lid, "what is gradient descent", [])["meta"]["retrieval"]
        self.assertEqual((meta["kind"], meta["semantic"], meta["degraded"]), ("lexical_subword", False, False))


class TestGracefulFailure(Base):
    def test_failing_provider_degrades_to_bm25_and_still_answers(self):
        r = self.r(Failing())
        self.assertEqual((r["kind"], r["mode"], r["semantic"], r["degraded"]), ("bm25_only", "lexical", False, True))
        self.assertEqual(r["degraded_reason"], "TimeoutError")
        self.assertTrue(r["chunks"] and r["sufficient"])                          # BM25 still finds it
        self.assertTrue(all(c["scores"]["vector_sim"] is None for c in r["chunks"]))

    def test_answer_still_grounded_when_provider_down(self):
        svc = ask.AskService(self.repo, self.env.storage, None, Failing())
        a = svc._answer(self.lid, "what is gradient descent", [])
        self.assertTrue(a["grounded"] and a["cited"])
        self.assertEqual((a["meta"]["retrieval"]["kind"], a["meta"]["retrieval"]["degraded"]), ("bm25_only", True))

    def test_cooldown_stops_hammering_a_failing_provider(self):
        with mock.patch.dict(os.environ, {"NOTIV_EMBED_COOLDOWN_SECONDS": "60"}):
            self.r(Failing()); n = Failing.calls
            r2 = self.r(Failing())
            self.assertEqual(Failing.calls, n)                                      # not called again during cool-down
            self.assertEqual((r2["kind"], r2["degraded"]), ("bm25_only", True))
            self.assertTrue(retrieval.retrieval_status()["cooling_down"])

    def test_cooldown_expires_and_provider_recovery_is_picked_up(self):
        with mock.patch.dict(os.environ, {"NOTIV_EMBED_COOLDOWN_SECONDS": "0"}):
            self.r(Failing()); self.r(Failing())
            self.assertGreaterEqual(Failing.calls, 2)                               # no cool-down -> retried
        retrieval.reset_breaker()
        self.assertEqual(self.r(NeuralStandIn())["kind"], "semantic")

    def test_no_mixing_of_neural_vectors_with_fallback_after_outage(self):
        self.assertEqual(self.r(NeuralStandIn())["kind"], "semantic")               # neural vectors now stored
        retrieval.reset_breaker()

        class OutageLater(NeuralStandIn):                                            # same model name, now failing at query time
            def embed(self, texts):
                raise EmbeddingUnavailable("ConnectionError")
        r = self.r(OutageLater())
        self.assertEqual((r["kind"], r["degraded"]), ("bm25_only", True))
        self.assertTrue(all(c["scores"]["vector_sim"] is None for c in r["chunks"]))

    def test_degradation_is_counted_in_metrics(self):
        from core.lecture import obs
        obs.reset_metrics(); self.r(Failing())
        self.assertIn("notiv_retrieval_degraded_total", obs.render_prometheus())


class TestProviderConfiguration(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        for k in ("NOTIV_EMBEDDINGS", "MISTRAL_API_KEY", "NOTIV_ENV"):
            os.environ.pop(k, None)
        retrieval.reset_breaker()

    def tearDown(self):
        os.environ.clear(); os.environ.update(self._env)

    def test_default_is_local_and_clean(self):
        c = retrieval.embedding_config()
        self.assertEqual((c["effective"], c["neural"], c["problems"]), ("local", False, []))
        self.assertEqual(retrieval.retrieval_status()["kind"], "lexical_subword")
        self.assertIsInstance(retrieval.default_embedder(), SubwordHashEmbedder)

    def test_unknown_value_is_a_loud_problem_not_silently_semantic(self):
        os.environ["NOTIV_EMBEDDINGS"] = "mistrall"
        c = retrieval.embedding_config()
        self.assertEqual((c["effective"], c["neural"]), ("local", False)); self.assertTrue(c["problems"])
        self.assertTrue(any("NOTIV_EMBEDDINGS" in p for p in settings.problems("api")))         # reported even in development

    def test_mistral_without_key_falls_back_and_is_reported(self):
        os.environ["NOTIV_EMBEDDINGS"] = "mistral"
        c = retrieval.embedding_config()
        self.assertEqual((c["effective"], c["neural"]), ("local", False))
        self.assertTrue(any("MISTRAL_API_KEY" in p for p in c["problems"]))
        self.assertTrue(retrieval.retrieval_status()["degraded"])
        os.environ["NOTIV_ENV"] = "production"
        self.assertTrue(any("MISTRAL_API_KEY" in p for p in settings.problems("worker")))       # production refuses to start
        os.environ.pop("NOTIV_ENV")
        self.assertFalse(any("MISTRAL_API_KEY" in p for p in settings.problems("worker")))      # dev: not fatal

    def test_mistral_embedder_fails_cleanly_without_key_or_package(self):
        with self.assertRaises(EmbeddingUnavailable) as cm: retrieval.MistralEmbedder().embed(["x"])
        self.assertIn("MISTRAL_API_KEY", str(cm.exception))
        os.environ["MISTRAL_API_KEY"] = "k" * 20
        import importlib.util
        if importlib.util.find_spec("langchain_mistralai") is None:
            with self.assertRaises(EmbeddingUnavailable) as cm: retrieval.MistralEmbedder().embed(["x"])
            self.assertIn("not installed", str(cm.exception))

    def test_readiness_reports_what_retrieval_really_is(self):
        d = health.readiness()["checks"]["retrieval"]
        self.assertTrue(d["ok"]); self.assertIn("NOT semantic", d["detail"])


if __name__ == "__main__":
    unittest.main()
