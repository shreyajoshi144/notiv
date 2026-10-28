"""Regression tests for the failures found on the 39-page handwritten System Design notes (systemdesgin.pdf):

  1. a garbled embedded text layer was trusted as evidence  -> text_quality + vision/OCR recovery, else excluded + reported
  2. OpenAI "no credits" (HTTP 429 insufficient_quota) was shown as "busy, try again in a minute" -> quota_exhausted
  3. OPENAI_MODEL was ignored, and pointed at a model OpenAI does not serve -> honoured + checked against the endpoint
  4. summaries were excerpts, concepts were junk page titles, 0 flashcards, maps without edges -> validated AI artifact

All model calls here are FAKES: these tests prove the plumbing, validation and storage, not the quality of real AI output
(that is tools/e2e_system_design.py, which uses real providers).
"""
import json
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest import mock

from core.lecture import ai_study, knowledge, llm, llm_openai, slides
from core.lecture.make_test_lecture import make_text_pdf
from core.lecture.study import StudyService
from core.lecture.testkit import Env
from core.lecture.workspace import WorkspaceService

# real text-layer content of pages 7, 9 and 12 of systemdesgin.pdf (handwriting app "recognition")
GARBLED = [
    "Wortdof\nDatabases\n=\n\nS9Lvs.NoS÷\n\niaa\n1) structured\n2)\n\nPredefined\n\n3) Data\n\nin\n\nRow\n\nOne\n\ncolumn\n\nD\n\ns",
    "if÷÷÷÷f÷÷÷i÷÷÷:ff÷÷:f÷÷i÷÷÷\n.\n\nE\nu\n\nw\n\nO\n\nor\n\nj\nor\n\nch\n\nCs\n\nO\n\no\n\n→\n\nE\n\nd\n\ns\nd\n\nf 6\n\n" * 3,
    "CAP-heore€\n\nAchieved\n\nby\n\nupdating\n\nseveral\n\nbefore\n\nallowing\n\n\\tenyC\n\nreads\n\nnodes\n\nHr\n\nat\n\n\"\n\n[\n\n",
]
GOOD = ["Gradient Descent\nUpdate rule: w = w - lr * gradient\nLoss: L = (1/n) * sum((y - y_hat)^2)",
        "Agenda\n- Intro\n- Methods\n- Results\n- Q&A",
        "Relational databases\n- MySQL\n- Oracle\n- Postgres\n- SQLite\n- MariaDB\n- MS SQL Server",
        "Photosynthesis is defined as the process by which green plants use sunlight to synthesize foods from carbon "
        "dioxide and water. Cellular respiration is the process by which cells break down glucose to release energy."]
SCATTERED = ["Load", "", "Balancing", "", "Types", "", "of", "", "distribution", "", "Random", "", "Round", "", "robin", "",
             "Smart", "", "clients", "", "detects", "", "hosts", "", "HAProxy"]
TRANSCRIBED = ("Load Balancing\nTypes of distribution: Random, Round-robin, weighted\n"
               "A load balancer spreads requests across servers so no single server is overloaded.\n"
               "Smart clients take a pool of service hosts and balance load; they detect hosts that are not responsive.\n"
               "HAProxy is an open source software load balancer.")


class TestTextQuality(unittest.TestCase):
    def test_garbled_layers_from_the_real_pdf_are_rejected(self):
        for t in GARBLED:
            q = slides.text_quality(t)
            self.assertFalse(q["ok"], (t[:30], q))

    def test_good_text_is_kept(self):
        for t in GOOD:
            q = slides.text_quality(t)
            self.assertTrue(q["ok"], (t[:30], q))


def make_scattered_pdf(path: str, words=SCATTERED) -> str:
    """A PDF whose text layer scatters single words far apart, as the handwriting app's layer does (pdftotext then emits
    one word per line with blank lines between)."""
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(path, pagesize=(454, 588))
    c.setFont("Helvetica", 10)
    for i, w in enumerate(x for x in words if x):
        c.drawString(40 + (i * 53) % 300, 560 - i * 36, w)
    c.showPage()
    c.save()
    return path


class FakeVision:
    """Stands in for an image-capable LLMChain."""
    def __init__(self, text=TRANSCRIBED, error=None):
        self.text, self.error, self.calls = text, error, 0
        self.providers = [object()]

    def __bool__(self): return True

    def see(self, system, user, png):
        self.calls += 1
        assert png.startswith(b"\x89PNG")
        if self.error:
            raise self.error
        return llm.Completion(self.text, "groq", "qwen/qwen3.8-27b")


class TestRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # a PDF whose embedded text layer is the scattered-word kind (what the handwriting app produced)
        self.pdf = make_scattered_pdf(os.path.join(self.tmp, "scattered.pdf"))
        for k, v in (("OCR_MODE", "off"), ("VISION_MODE", "auto")):
            p = mock.patch.object(slides, k, v); p.start(); self.addCleanup(p.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def out(self):
        return tempfile.mkdtemp(dir=self.tmp)

    def test_garbled_layer_is_reread_from_the_page_image(self):
        v = FakeVision()
        [s] = slides.load_deck(self.pdf, self.out(), vision=v)
        self.assertEqual((s.text_source, s.transcribed_by), ("vision", "groq/qwen/qwen3.8-27b"))
        self.assertIn("load balancer spreads requests", s.text)
        self.assertIn("low_quality_text", s.warnings)                   # still reported
        self.assertFalse(s.quality["ok"])
        self.assertEqual(v.calls, 1)

    def test_unrecoverable_garbled_layer_is_excluded_not_used(self):
        [s] = slides.load_deck(self.pdf, self.out(), vision=None)
        self.assertEqual((s.text, s.text_source), ("", "none"))
        self.assertIn("unreadable_text_excluded", s.warnings)

    def test_vision_failure_is_reported_and_text_excluded(self):
        v = FakeVision(error=llm.LLMUnavailable([("groq", "quota_exhausted")]))
        [s] = slides.load_deck(self.pdf, self.out(), vision=v)
        self.assertEqual(s.text, "")
        self.assertIn("vision_failed", s.warnings)

    def test_vision_garbage_is_not_accepted(self):
        [s] = slides.load_deck(self.pdf, self.out(), vision=FakeVision("[no readable text]"))
        self.assertEqual(s.text, "")
        self.assertIn("vision_unreadable", s.warnings)

    def test_garbled_handwriting_layer_is_never_ocrd_into_evidence(self):
        """Regression (live run): Tesseract on handwritten pages returned confident-looking junk ("fay ofl of __y | Hash
        funtion") that passed and was summarised. A garbled (handwriting-app) layer may only be recovered by vision."""
        with mock.patch.object(slides, "OCR_MODE", "auto"), mock.patch.object(slides, "ocr_available", lambda: True), \
             mock.patch.object(slides, "_ocr_page", lambda *a: self.fail("handwriting must not be OCR'd")):
            [s] = slides.load_deck(self.pdf, self.out(), vision=None)
        self.assertEqual((s.text, s.text_source), ("", "none"))
        self.assertIn("unreadable_text_excluded", s.warnings)

    def test_good_text_layer_never_calls_vision(self):
        pdf = make_text_pdf(os.path.join(self.tmp, "fx"), n=1, slide_lines=[GOOD[0].split("\n")], name="good.pdf")
        v = FakeVision()
        [s] = slides.load_deck(pdf, self.out(), vision=v)
        self.assertEqual((s.text_source, v.calls), ("pdf_text", 0))


class SDKErr(Exception):
    """Shaped like an openai/anthropic APIStatusError."""
    def __init__(self, status, code=None, etype=None, retry=None):
        super().__init__(f"HTTP {status}")
        self.status_code = status
        self.body = {"error": {"code": code, "type": etype, "message": "secret-free text"}}
        self.response = NS(status_code=status, headers={"retry-after": str(retry)} if retry is not None else {})


class TestProviderDiagnosis(unittest.TestCase):
    def test_openai_no_credits_is_quota_exhausted_not_rate_limited(self):
        e = SDKErr(429, "credit_balance_exhausted", "insufficient_quota")
        self.assertEqual(llm.classify(e), "quota_exhausted")
        self.assertEqual(llm.classify(SDKErr(429, "insufficient_quota", "insufficient_quota")), "quota_exhausted")
        self.assertEqual(llm.classify(SDKErr(429, "rate_limit_exceeded", "tokens", retry=18)), "rate_limited")
        self.assertEqual(llm.classify(SDKErr(404, "model_not_found", "invalid_request_error")), "unsupported_model")
        self.assertEqual(llm.classify(SDKErr(503, None, "internal_server_error")), "unavailable")

    def test_daily_limit_is_reported_and_not_waited_for(self):
        """Regression (live run): Groq's free tier allows 200k tokens/day for the vision model; when used up, Groq says
        "try again in 14m44s" — that is not a short rate limit, and every remaining page must not burn a doomed request."""
        e = SDKErr(429, "rate_limit_exceeded", "tokens", retry=885)
        e.body["error"]["message"] = ("Rate limit reached for model `qwen/qwen3.8-27b` ... on tokens per day (TPD): Limit 200000, "
                                      "Used 199717, Requested 2331. Please try again in 14m44.736s.")
        self.assertEqual(llm.classify(e), "daily_limit")
        self.assertIn("daily", llm.LLMUnavailable([("groq", "daily_limit")]).describe())
        self.assertFalse(llm.LLMUnavailable([("groq", "daily_limit")]).transient)
        self.assertIn("daily_limit", llm.HARD)

    def test_describe_says_what_happened(self):
        msg = llm.LLMUnavailable([("openai", "quota_exhausted"), ("groq", "rate_limited")]).describe()
        self.assertIn("Openai has no API credits", msg)
        self.assertIn("Groq is rate-limiting", msg)

    def test_hard_failures_cool_down_longer_than_transient_ones(self):
        class P(llm.Provider):
            def __init__(self, name, err): super().__init__(name); self.name, self.err = name, err
            def configured(self): return True
            def installed(self): return True
            def complete(self, *a): raise self.err
        t = [1000.0]
        with mock.patch.dict(os.environ, {"NOTIV_LLM_COOLDOWN_SECONDS": "30", "NOTIV_LLM_HARD_COOLDOWN_SECONDS": "600"}):
            chain = llm.LLMChain([P("openai", SDKErr(429, "insufficient_quota", "insufficient_quota")),
                                  P("groq", SDKErr(429, "rate_limit_exceeded", retry=18))], clock=lambda: t[0])
        with self.assertRaises(llm.LLMUnavailable) as cm:
            chain("s", "u")
        self.assertEqual(cm.exception.retry_after, 18.0)
        self.assertAlmostEqual(chain._down["openai"] - 1000, 600)
        self.assertAlmostEqual(chain._down["groq"] - 1000, 18)

    def test_batch_backoff_waits_out_rate_limits_but_not_missing_credits(self):
        calls, slept = [], []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise llm.LLMUnavailable([("groq", "rate_limited")], retry_after=17)
            return "done"
        self.assertEqual(llm.call_with_backoff(flaky, 300, sleep=slept.append, clock=lambda: 0), "done")
        self.assertEqual(slept, [17.5, 17.5])

        def broke():
            raise llm.LLMUnavailable([("openai", "quota_exhausted")])
        with self.assertRaises(llm.LLMUnavailable):
            llm.call_with_backoff(broke, 300, sleep=slept.append, clock=lambda: 0)
        self.assertEqual(len(slept), 2)                                  # no waiting for a failure time cannot fix

    def test_batch_waits_for_a_rate_limited_provider_when_another_has_no_credits(self):
        """Regression (live run on systemdesgin.pdf): OpenAI had no credits AND Groq was rate-limited; the batch gave up
        at once instead of waiting for Groq, so AI study material failed and 13 pages missed vision transcription."""
        calls, slept = [], []

        def mixed():
            calls.append(1)
            if len(calls) < 2:
                raise llm.LLMUnavailable([("openai", "quota_exhausted"), ("groq", "rate_limited")], retry_after=12)
            return "groq answered"
        self.assertEqual(llm.call_with_backoff(mixed, 300, sleep=slept.append, clock=lambda: 0), "groq answered")
        self.assertEqual(slept, [12.5])

    def test_openai_model_env_is_honoured_and_checked_against_the_endpoint(self):
        env = {"OPENAI_API_KEY": "k", "OPENAI_MODEL": "openai/gpt-oss-120b", "NOTIV_OPENAI_MODEL": "", "OPENAI_BASE_URL": ""}
        with mock.patch.dict(os.environ, env):
            p = llm_openai.OpenAIProvider.from_env()
        self.assertEqual(p.model, "openai/gpt-oss-120b")                  # was silently ignored before
        p._client = NS(models=NS(list=lambda: [NS(id="gpt-6-astra"), NS(id="gpt-6-luna")]),
                       responses=NS(create=lambda **k: self.fail("must not call a model the endpoint does not serve")))
        with self.assertRaises(llm.LLMError) as cm:
            p.complete("s", "u", None, "x")
        self.assertEqual(cm.exception.reason, "unsupported_model")
        with mock.patch.dict(os.environ, {**env, "NOTIV_OPENAI_MODEL": "gpt-6-luna"}):
            self.assertEqual(llm_openai.OpenAIProvider.from_env().model, "gpt-6-luna")   # NOTIV_* wins

    def test_groq_provider(self):
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "g", "NOTIV_GROQ_MODEL": "", "NOTIV_GROQ_VISION_MODEL": ""}):
            p = llm_openai.GroqProvider.from_env()
            self.assertTrue(p.configured())
        self.assertEqual((p.base_url, p.model, p.vision_model),
                         ("https://api.groq.com/openai/v1", "openai/gpt-oss-120b", "qwen/qwen3.8-27b"))
        sent = {}
        p._listed = {"openai/gpt-oss-120b", "qwen/qwen3.8-27b"}
        p._client = NS(chat=NS(completions=NS(create=lambda **k: sent.update(k) or NS(choices=[NS(finish_reason="stop", message=NS(content="Load Balancing"))]))))
        self.assertEqual(p.see("sys", "Transcribe this page.", b"\x89PNG..."), "Load Balancing")
        self.assertEqual(sent["model"], "qwen/qwen3.8-27b")
        self.assertTrue(sent["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))


# ── AI study artifact ────────────────────────────────────────────────────
class FakeChain:
    """Plays the answer model: returns canned JSON per schema name, recording what it was shown."""
    def __init__(self, map_out, study_out, synth_out):
        self.out = {"study_map": map_out, "study_questions": study_out, "study_overview": synth_out}
        self.seen = []
        self.providers = [object()]

    def __bool__(self): return True

    def complete(self, system, user, schema=None, schema_name="output"):
        self.seen.append((schema_name, user))
        out = self.out[schema_name]
        return llm.Completion(json.dumps(out(user) if callable(out) else out), "groq", "openai/gpt-oss-120b")


def eid(user, needle):
    for line in user.split("\n"):
        if needle.lower() in line.lower() and line.startswith("[E"):
            return int(line[2:line.index("]")])
    raise AssertionError(f"{needle!r} not shown to the model")


PAGES = [["CAP Theorem", "Consistency: all nodes see the same data at the same time.",
          "Availability: every request gets a response.", "Partition tolerance: the system keeps working despite network failure."],
         ["Caching", "A cache stores frequently used data in memory to reduce database load.", "Write-through cache updates the cache and DB together."]]


def map_out(user):
    cap, cache, avail = eid(user, "Consistency:"), eid(user, "A cache stores"), eid(user, "Availability:")
    return {"sections": [{"title": "CAP theorem", "summary": "A distributed store cannot guarantee consistency, availability and partition tolerance all at once; designers choose two.", "evidence_ids": [cap, avail]},
                         {"title": "Caching", "summary": "Caches keep hot data in memory so the database is queried less often.", "evidence_ids": [cache]},
                         {"title": "Invented", "summary": "This cites nothing real.", "evidence_ids": [999999]}],
            "concepts": [{"name": "Consistency", "source_definition": "All nodes see the same data at the same time.", "definition_evidence_ids": [cap], "explanation": "Every read returns the latest write.", "mentioned_in": [cap]},
                         {"name": "Cache", "source_definition": "", "definition_evidence_ids": [], "explanation": "Fast storage layer.", "mentioned_in": [cache]},
                         {"name": "Availability", "source_definition": "Every request gets a response.", "definition_evidence_ids": [], "explanation": "The system answers.", "mentioned_in": [avail]},
                         {"name": "Quantum sharding", "source_definition": "Made up.", "definition_evidence_ids": [cap], "explanation": "x", "mentioned_in": [cap]}],
            "relations": [{"source": "Consistency", "target": "Availability", "label": "trades off against", "evidence_ids": [cap]},
                          {"source": "Consistency", "target": "Nonexistent", "label": "uses", "evidence_ids": [cap]}],
            "examples": [], "comparisons": [{"a": "Consistency", "b": "Availability", "points": ["one gives up the other under partition"], "evidence_ids": [cap, avail]}]}


def study_out(user):
    cap, cache = eid(user, "Consistency:"), eid(user, "A cache stores")
    return {"flashcards": [{"question": "What does the CAP theorem say?", "answer": "A distributed system cannot provide consistency, availability and partition tolerance at the same time.", "type": "explain", "evidence_ids": [cap]},
                           {"question": "Uncited card", "answer": "x", "type": "explain", "evidence_ids": []}],
            "quiz": [{"question": "Why add a cache?", "options": ["Reduce database load", "Increase disk use", "Encrypt data", "Add latency"], "correct_index": 0, "explanation": "Hot data is served from memory.", "evidence_ids": [cache]},
                     {"question": "Bad options", "options": ["a", "a", "b", "c"], "correct_index": 0, "explanation": "", "evidence_ids": [cache]},
                     {"question": "Bad index", "options": ["a", "b", "c", "d"], "correct_index": 7, "explanation": "", "evidence_ids": [cache]}]}


def synth_out(user):
    ids = [int(x) for x in __import__("re").findall(r"E(\d+)", user)]
    return {"title": "Distributed systems notes", "overview": "The notes cover the CAP theorem and caching. They explain the trade-off between consistency and availability. They then show how caches reduce database load. Together they describe core scaling choices. Each idea is tied to the pages that state it.",
            "key_takeaways": [{"text": "You can only guarantee two of consistency, availability and partition tolerance.", "evidence_ids": ids[:1]},
                              {"text": "Uncited takeaway", "evidence_ids": [424242]}],
            "topics": [{"name": "Distributed data", "summary": "Trade-offs and caching.", "section_titles": ["CAP theorem", "Caching"], "concepts": ["Consistency", "Availability", "Cache"]}]}


class AiBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = Env(self.tmp)
        pdf = make_text_pdf(os.path.join(self.tmp, "fx"), n=2, slide_lines=PAGES, name="notes.pdf")
        self.lid = self.env.lecture(deck=pdf)
        self.env.process(self.lid)
        self.chain = FakeChain(map_out, study_out, synth_out)
        p = mock.patch.dict(os.environ, {"NOTIV_AI_STUDY": "auto"}); p.start(); self.addCleanup(p.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def gen(self):
        return ai_study.ensure(self.env.repo, self.lid, self.chain, force=True, max_wait=0)


class TestAiStudyValidation(AiBase):
    def test_generation_validates_everything_before_saving(self):
        r = self.gen()
        self.assertEqual(r["status"], "ok", r)
        ai = ai_study.load(self.env.repo, self.lid)
        self.assertEqual([s["title"] for s in ai["sections"]], ["CAP theorem", "Caching"])        # invented-id section dropped
        names = {c["name"]: c for c in ai["concepts"]}
        self.assertNotIn("Quantum sharding", names)                                         # not named in its evidence
        self.assertTrue(names["Consistency"]["source_supported"])
        self.assertFalse(names["Availability"]["source_supported"])                         # definition without a citation
        self.assertEqual(names["Availability"]["definition"], "")
        self.assertEqual(names["Cache"]["definition"], "")                                  # source names it, never explains
        self.assertTrue(names["Cache"]["explanation"])                                      # supplementary kept, separately
        self.assertEqual([(r["source"], r["target"], r["label"]) for r in ai["relations"]],
                         [("Consistency", "Availability", "trades off against")])
        self.assertEqual(len(ai["flashcards"]), 1)
        self.assertEqual([q["question"] for q in ai["quiz"]], ["Why add a cache?"])
        self.assertEqual(len(ai["takeaways"]), 1)
        self.assertEqual(ai["sections"][0]["pages"], [1])                                  # pages from evidence, not the model
        self.assertEqual(ai["sections"][1]["pages"], [2])
        dropped = ai["stats"]["dropped"]
        for k in ("sections", "concepts", "unsupported_definitions", "relations", "flashcards", "quiz", "takeaways"):
            self.assertGreaterEqual(dropped.get(k, 0), 1, k)
        self.assertTrue(all(s.startswith(("Part", "-")) or "[E" in s for _, s in self.chain.seen))

    def test_artifact_goes_stale_when_evidence_changes(self):
        self.gen()
        self.assertIsNotNone(ai_study.load(self.env.repo, self.lid))
        ev = self.env.repo.list_evidence(self.lid)[0]
        from core.lecture import store
        store.conn().execute("UPDATE lecture_evidence SET text=text||' edited' WHERE id=?", (ev["id"],)); store.conn().commit()
        self.assertIsNone(ai_study.load(self.env.repo, self.lid))

    def test_provider_failure_is_reported_never_faked(self):
        class Down:
            providers = [object()]
            def __bool__(self): return True
            def complete(self, *a, **k): raise llm.LLMUnavailable([("openai", "quota_exhausted")])
        r = ai_study.ensure(self.env.repo, self.lid, Down(), force=True, max_wait=0)
        self.assertEqual(r["status"], "failed")
        self.assertIn("no API credits", r["message"])
        self.assertIsNone(ai_study.load(self.env.repo, self.lid))
        self.assertEqual(ai_study.ensure(self.env.repo, self.lid, llm.LLMChain([]), force=True)["status"], "no_provider")


class TestAiStudyServed(AiBase):
    def setUp(self):
        super().setUp()
        self.gen()
        knowledge.regenerate(self.env.repo, self.lid)

    def test_knowledge_is_ai_and_cited_with_supplementary_kept_apart(self):
        k = StudyService(self.env.repo, self.env.storage).knowledge(1, self.lid)
        concepts = {i["title"]: i for i in k["items"] if i["kind"] == "concept"}
        self.assertEqual(set(concepts), {"Consistency", "Availability", "Cache"})
        c = concepts["Consistency"]
        self.assertEqual((c["body"], c["source_supported"], c["origin"]), ("All nodes see the same data at the same time.", True, "ai_generated"))
        self.assertEqual(c["supplementary_explanation"], "Every read returns the latest write.")
        self.assertIn("not from your notes", c["supplementary_label"])
        self.assertIsNone(concepts["Cache"]["body"])
        self.assertIn("do not explain", concepts["Cache"]["not_in_source_note"])
        self.assertTrue(all(i["evidence"] for i in k["items"]))
        defs = [i for i in k["items"] if i["kind"] == "definition"]
        self.assertEqual([d["title"] for d in defs], ["Consistency"])

    def test_flashcards_and_quiz_come_from_the_ai_artifact(self):
        st = StudyService(self.env.repo, self.env.storage)
        st.generate_items(1, self.lid, ["flashcard", "mcq"])
        cards = st.list_items(1, self.lid, kind="flashcard", reveal=True)["items"]
        mcq = st.list_items(1, self.lid, kind="mcq", reveal=True)["items"]
        self.assertEqual([c["prompt"] for c in cards], ["What does the CAP theorem say?"])
        self.assertEqual(mcq[0]["correct_option"], "a")
        self.assertEqual(mcq[0]["options"][0]["text"], "Reduce database load")
        self.assertTrue(all(i["evidence"] for i in cards + mcq))

    def test_summary_is_ai_prose_in_the_existing_section_shape(self):
        ws = WorkspaceService(self.env.repo, self.env.storage)
        su = ws.summary(1, self.lid)
        self.assertEqual((su["mode"], su["provider"]), ("ai", "groq"))
        self.assertIn("CAP theorem", su["overview"])
        for s in su["sections"]:
            for key in ("id", "kind", "title", "body", "evidence_ids", "evidence", "slide_index", "seek_seconds", "origin_label"):
                self.assertIn(key, s)
            self.assertTrue(s["evidence"])
        self.assertEqual(ws.overview(1, self.lid)["counts"]["summary_sections"], 2)

    def test_mind_map_has_labelled_edges_and_page_refs(self):
        mm = WorkspaceService(self.env.repo, self.env.storage).mind_map(1, self.lid)
        texts = {n["id"]: n["text"] for n in mm["nodes"]}
        self.assertIn("Distributed data", texts.values())
        rel = [(texts[e["from"]], e["label"], texts[e["to"]]) for e in mm["edges"]]
        self.assertIn(("Consistency", "trades off against", "Availability"), rel)
        self.assertIn(("Distributed data", "covers", "Cache"), rel)
        self.assertTrue(all(n["source"]["pages"] for n in mm["nodes"]))

    def test_other_users_cannot_read_ai_material(self):
        from core.lecture.errors import NotFoundError
        with self.assertRaises(NotFoundError):
            WorkspaceService(self.env.repo, self.env.storage).summary(2, self.lid)
        with self.assertRaises(NotFoundError):
            WorkspaceService(self.env.repo, self.env.storage).mind_map(2, self.lid)

    def test_without_ai_the_old_extractive_path_still_works(self):
        lid = self.env.lecture(deck=make_text_pdf(os.path.join(self.tmp, "fx2"), n=1, slide_lines=[PAGES[1]], name="b.pdf"))
        self.env.process(lid)
        su = WorkspaceService(self.env.repo, self.env.storage).summary(1, lid)
        self.assertEqual(su["mode"], "extractive")
        mm = WorkspaceService(self.env.repo, self.env.storage).mind_map(1, lid)
        self.assertTrue(mm["insufficient_evidence"])


class TestAskSaysWhy(unittest.TestCase):
    def test_degraded_reason(self):
        from core.test_ask import ALICE, AskBase  # noqa: F401  (fixture lecture reused below)
        self.assertIn("no API credits", llm.LLMUnavailable([("openai", "quota_exhausted")]).describe())


if __name__ == "__main__":
    unittest.main()
