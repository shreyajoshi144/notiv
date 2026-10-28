"""Sprint 2 tests: lecture workspace — synchronization, evidence navigation,
search, Notiv-generated insights, authorization, processing states, handlers.

Everything runs through the real staged pipeline on a real encoded video + a
PDF deck with a text layer (make_test_lecture). Transcript lines are explicit
test INPUT (Whisper isn't available offline), exactly as in the Sprint 1 tests.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

from core.lecture import insights, jobs, pipeline
from core.lecture.align import Segment
from core.lecture.errors import NotFoundError, NotReadyError, ValidationError
from core.lecture.make_test_lecture import make_lecture, make_text_pdf
from core.lecture.provenance import origin_of
from core.lecture.sync import Sync
from core.lecture.testkit import Env, segments
from core.lecture.workspace import WorkspaceService

DURATIONS = [6, 9, 5, 7]            # slides change at 6, 15, 20 s; total 27 s
SEGS = [Segment(0.5, 4.0, "Today we cover gradient descent and the learning rate."),
        Segment(7.0, 12.0, "Backpropagation applies the chain rule through every layer. This is important to remember."),
        Segment(16.0, 19.0, "Regularization penalizes large weights to reduce overfitting. Remember this for the exam."),
        Segment(21.0, 26.0, "A convolution slides a kernel over the image.")]
ALICE, BOB, EVE = 1, 2, 3


# ───────────────────────── pure synchronization ─────────────────────────
class TestSync(unittest.TestCase):
    TL = [{"slide_index": 1, "start": 0.0, "end": 6.0}, {"slide_index": 2, "start": 6.0, "end": 15.0},
          {"slide_index": None, "start": 15.0, "end": 18.0}, {"slide_index": 3, "start": 18.0, "end": 27.0},
          {"slide_index": 1, "start": 27.0, "end": 30.0}]                      # slide 1 shown again at the end
    SEG = [{"id": 10, "start": 1.0, "end": 4.0, "slide_index": 1}, {"id": 11, "start": 7.0, "end": 12.0, "slide_index": 2},
           {"id": 12, "start": 11.5, "end": 14.0, "slide_index": 2},          # overlaps the previous one
           {"id": 13, "start": 20.0, "end": 25.0, "slide_index": 3}]

    def setUp(self):
        self.s = Sync(self.TL, self.SEG)

    def test_time_to_slide_boundaries(self):
        for t, want in ((0, 1), (5.99, 1), (6.0, 2), (14.99, 2), (15.0, None), (17.9, None), (18.0, 3), (30.0, 1)):
            self.assertEqual(self.s.slide_at(t), want, t)
        self.assertIsNone(self.s.slide_at(30.01))                              # past the end
        self.assertIsNone(self.s.slide_at(-1))

    def test_time_to_segment_including_gaps_and_overlap(self):
        self.assertEqual(self.s.segment_at(1.0)["id"], 10)
        self.assertIsNone(self.s.segment_at(5.0))                              # gap between lines
        self.assertEqual(self.s.segment_at(7.0)["id"], 11)
        self.assertEqual(self.s.segment_at(13.0)["id"], 12)                    # overlap: later-starting segment wins
        self.assertIsNone(self.s.segment_at(25.0))                             # end is exclusive

    def test_slide_to_time_uses_first_appearance_then_alignment_then_none(self):
        self.assertEqual(self.s.seek_for_slide(1), 0.0)                        # first of two appearances
        self.assertEqual(self.s.seek_for_slide(3), 18.0)
        only_seg = Sync([], [{"id": 1, "start": 9.0, "end": 12.0, "slide_index": 4}])
        self.assertEqual(only_seg.seek_for_slide(4), 9.0)                      # no timeline: fall back to aligned segment
        self.assertIsNone(Sync([], []).seek_for_slide(1))                      # nothing known -> None, never invented

    def test_round_trip_time_slide_time_is_consistent(self):
        for slide in (1, 2, 3):
            t = self.s.seek_for_slide(slide)
            self.assertEqual(self.s.slide_at(t), slide)

    def test_position_for_segment_honours_stored_alignment(self):
        seg = dict(self.SEG[1], slide_index=3)                                  # student moved this line to slide 3
        self.assertEqual(self.s.position_for_segment(seg)["slide_index"], 3)    # stored (manual) alignment beats raw time lookup

    def test_unsorted_and_timeless_rows_are_tolerated(self):
        s = Sync(list(reversed(self.TL)) + [{"slide_index": 9, "start": None, "end": None}], list(reversed(self.SEG)))
        self.assertEqual(s.slide_at(7), 2)
        self.assertEqual(s.segment_at(1.5)["id"], 10)


class TestProvenance(unittest.TestCase):
    def test_origins(self):
        self.assertEqual(origin_of("transcript"), ("professor_said", "Professor said"))
        self.assertEqual(origin_of("slide"), ("slide_text", "Slide text"))
        self.assertEqual(origin_of("key_moment"), ("notiv_generated", "Notiv analysis"))     # anything else is generated


# ───────────────────────── fixture ─────────────────────────
class WsBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.video, _, _ = make_lecture(os.path.join(cls.tmp, "fx"), DURATIONS)
        cls.pdf = make_text_pdf(os.path.join(cls.tmp, "fx"))
        cls.env = Env(cls.tmp)
        cls.repo, cls.ws = cls.env.repo, WorkspaceService(cls.env.repo, cls.env.storage)
        cls.lid = cls.env.lecture(ALICE, cls.video, cls.pdf, title="Deep Learning 101")
        cls.env.process(cls.lid, segments(*SEGS))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)


class TestOverviewAndStates(WsBase):
    def test_ready_overview_has_real_counts_media_and_mode(self):
        ov = self.ws.overview(ALICE, self.lid)
        self.assertEqual((ov["state"], ov["ready"], ov["title"]), ("ready", True, "Deep Learning 101"))
        self.assertEqual(ov["counts"], {"slides": 4, "segments": 4, "evidence": 8, "key_moments": 6, "summary_sections": 4,
                                        "pages_without_text": 0, "ocr_pages": 0})
        self.assertEqual(ov["media"]["type"], "video")
        self.assertRegex(ov["media"]["url"], r"^/files/lectures/\d+/assets/[0-9a-f]{32}\.mp4\?exp=\d+&sig=[0-9a-f]+$")
        self.assertTrue(ov["mode"]["can_seek"] and ov["mode"]["has_slides"] and ov["mode"]["has_transcript"])
        self.assertAlmostEqual(ov["duration"], 27, delta=0.5)
        self.assertEqual(ov["warnings"], [])
        self.assertNotIn("storage_key", repr(ov))                                # keys never leak, only signed URLs

    def test_queued_and_running_expose_progress_and_hide_content(self):
        lid = self.env.lecture(ALICE, self.video, self.pdf, title="Queued one")
        self.env.jobs().enqueue(lid)
        ov = self.ws.overview(ALICE, lid)
        self.assertEqual((ov["state"], ov["ready"], ov["counts"], ov["media"]), ("queued", False, None, None))
        self.assertEqual(len(ov["inputs"]), 2)                                  # uploaded files are listed while processing
        for call in (self.ws.timeline, self.ws.transcript, self.ws.slides, self.ws.evidence, self.ws.key_moments, self.ws.summary):
            with self.assertRaises(NotReadyError) as c:
                call(ALICE, lid)
            self.assertEqual(c.exception.info["state"], "queued")
        with self.assertRaises(NotReadyError):
            self.ws.search(ALICE, lid, "gradient")

    def test_failed_state_carries_the_real_error_and_retry_flag(self):
        lid = self.env.lecture(ALICE, title="Empty")
        with self.assertRaises(Exception):
            self.env.process(lid, segments())
        ov = self.ws.overview(ALICE, lid)
        self.assertEqual((ov["state"], ov["ready"], ov["can_retry"]), ("failed", False, True))
        self.assertIn("neither a recording nor a slide deck", ov["error"])
        self.assertEqual(ov["stages"][0]["stage"], "VALIDATE")
        self.assertEqual(ov["stages"][0]["status"], "failed")
        with self.assertRaises(NotReadyError) as c:
            self.ws.transcript(ALICE, lid)
        self.assertIn("neither a recording", c.exception.info["error"])

    def test_pipeline_includes_analyze_and_it_ran(self):
        self.assertEqual(pipeline.STAGE_NAMES[-1], "ANALYZE")
        job = self.repo.list_jobs(self.lid)[-1]
        st = {s["stage"]: s for s in self.repo.list_stages(job["id"])}
        self.assertEqual(st["ANALYZE"]["status"], "completed")
        self.assertTrue(st["ANALYZE"]["output"]["generated"])


# ───────────────────────── timeline / transcript / slides ─────────────────────────
class TestContentEndpoints(WsBase):
    def test_timeline_matches_ground_truth_and_marks_key_moments(self):
        tl = self.ws.timeline(ALICE, self.lid)
        self.assertEqual([s["slide_index"] for s in tl["spans"]], [1, 2, 3, 4])
        for span, truth in zip(tl["spans"][1:], (6, 15, 20)):
            self.assertAlmostEqual(span["start"], truth, delta=0.6)
        self.assertEqual(tl["spans"][2]["title"], "Regularization")
        self.assertTrue(all(m["origin"] == "notiv_generated" for m in tl["key_moments"]))
        self.assertEqual(len(tl["key_moments"]), 6)

    def test_transcript_every_line_has_timestamp_source_slide_and_citation(self):
        tr = self.ws.transcript(ALICE, self.lid)
        self.assertEqual([s["slide_index"] for s in tr["segments"]], [1, 2, 3, 4])
        s = tr["segments"][1]
        self.assertEqual((s["timestamp"], s["start"], s["origin"], s["origin_label"]), ("00:07", 7.0, "professor_said", "Professor said"))
        self.assertEqual(s["citation"]["label"], "Slide 2 · 00:07")
        self.assertEqual((s["citation"]["seek_seconds"], s["citation"]["source"]["type"]), (7.0, "video"))
        self.assertIsNotNone(s["evidence_id"])

    def test_transcript_filters_and_paging(self):
        self.assertEqual([s["start"] for s in self.ws.transcript(ALICE, self.lid, start=10, end=17)["segments"]], [7.0, 16.0])
        self.assertEqual([s["slide_index"] for s in self.ws.transcript(ALICE, self.lid, slide_index=3)["segments"]], [3])
        p1 = self.ws.transcript(ALICE, self.lid, limit=2)
        self.assertTrue(p1["has_more"])
        p2 = self.ws.transcript(ALICE, self.lid, after_id=p1["next_after_id"], limit=2)
        self.assertEqual([s["start"] for s in p1["segments"] + p2["segments"]], [0.5, 7.0, 16.0, 21.0])
        self.assertFalse(p2["has_more"])

    def test_slides_have_text_signed_images_seek_and_source(self):
        sl = self.ws.slides(ALICE, self.lid)["slides"]
        self.assertEqual([s["title"] for s in sl], ["Gradient Descent", "Backpropagation", "Regularization", "Convolutions"])
        self.assertAlmostEqual(sl[2]["seek_seconds"], 15.0, delta=0.6)
        self.assertRegex(sl[0]["image_url"], r"^/files/lectures/\d+/derived/slides/1\.png\?exp=\d+&sig=")
        self.assertEqual((sl[0]["origin"], sl[0]["citation"]["source"]["type"]), ("slide_text", "pdf"))
        self.assertIn("point 2 about regularization", sl[2]["text"])
        self.assertEqual(sl[1]["citation"]["label"].split(" · ")[0], "Slide 2")


# ───────────────────────── evidence ─────────────────────────
class TestEvidence(WsBase):
    def _all(self):
        return self.ws.evidence(ALICE, self.lid)["items"]

    def test_every_item_has_timestamp_and_source(self):
        items = self._all()
        self.assertEqual(len(items), 8)
        for it in items:
            c = it["citation"]
            self.assertIsNotNone(c["seek_seconds"], it)                         # transcript: own start; slide: first appearance
            self.assertIsNotNone(c["source"], it)
            self.assertIsNotNone(c["slide_index"], it)
            self.assertTrue(it["is_lecture_evidence"])
        kinds = {it["source_type"]: (it["origin"], it["origin_label"], it["citation"]["source"]["type"]) for it in items}
        self.assertEqual(kinds, {"transcript": ("professor_said", "Professor said", "video"), "slide": ("slide_text", "Slide text", "pdf")})

    def test_evidence_navigation_resolves_to_the_correct_position(self):
        by_text = {it["text"][:12]: it for it in self._all()}
        reg = by_text["Regularizati"]                                          # transcript line at 16.0 on slide 3
        self.assertEqual(reg["source_type"], "transcript") if False else None
        tline = next(i for i in self._all() if i["source_type"] == "transcript" and i["text"].startswith("Regularization"))
        pos = self.ws.locate(ALICE, self.lid, evidence_id=tline["evidence_id"])
        self.assertEqual((pos["seek_seconds"], pos["slide_index"]), (16.0, 3))
        seg = next(s for s in self.ws.transcript(ALICE, self.lid)["segments"] if s["evidence_id"] == tline["evidence_id"])
        self.assertEqual(pos["segment_id"], seg["segment_id"])
        sl = next(i for i in self._all() if i["source_type"] == "slide" and i["citation"]["slide_index"] == 4)
        pos = self.ws.locate(ALICE, self.lid, evidence_id=sl["evidence_id"])
        self.assertEqual(pos["slide_index"], 4)
        self.assertAlmostEqual(pos["seek_seconds"], 20.0, delta=0.6)             # first appearance of slide 4 in the video

    def test_filters(self):
        self.assertEqual({i["source_type"] for i in self.ws.evidence(ALICE, self.lid, source_type="slide")["items"]}, {"slide"})
        self.assertEqual(len(self.ws.evidence(ALICE, self.lid, slide_index=2)["items"]), 2)
        self.assertEqual(len(self.ws.evidence(ALICE, self.lid, limit=3)["items"]), 3)
        p = self.ws.evidence(ALICE, self.lid, limit=3)
        self.assertTrue(p["has_more"])
        self.assertEqual(len(self.ws.evidence(ALICE, self.lid, after_id=p["next_after_id"], limit=100)["items"]), 5)

    def test_item_lists_the_insights_it_supports(self):
        it = next(i for i in self._all() if i["text"].startswith("Regularization penalizes"))
        d = self.ws.evidence_item(ALICE, self.lid, it["evidence_id"])
        self.assertTrue({"key_moment", "summary_section"} <= {s["kind"] for s in d["supports"]})


# ───────────────────────── Notiv-generated insights ─────────────────────────
class TestInsights(WsBase):
    def test_key_moments_come_from_real_evidence_and_are_labelled_generated(self):
        km = self.ws.key_moments(ALICE, self.lid)
        self.assertEqual(km["origin"], "notiv_generated")
        ms = km["key_moments"]
        self.assertEqual([(m["method"]) for m in ms].count("rule:slide_change"), 4)
        self.assertEqual([m["reason"] for m in ms if m["method"] == "rule:emphasis_cue"],
                         ["Professor emphasis (reminder)", "Professor emphasis (exam relevance)"])
        real = {e["id"]: e for e in self.repo.list_evidence(self.lid)}
        for m in ms:
            self.assertEqual((m["origin"], m["origin_label"]), ("notiv_generated", "Notiv analysis"))
            self.assertTrue(m["evidence_ids"], "a key moment without supporting evidence must not exist")
            self.assertEqual([e["evidence_id"] for e in m["evidence"]], m["evidence_ids"])
            for e in m["evidence"]:
                self.assertTrue(e["is_lecture_evidence"])                      # support is lecture evidence, never generated text
                self.assertEqual(e["text"], real[e["evidence_id"]]["text"])    # verbatim, not paraphrased
            if m["start"] is not None:                                          # moment time must lie where its transcript evidence is
                for e in m["evidence"]:
                    if e["source_type"] == "transcript":
                        self.assertTrue(m["start"] - 1e-6 <= e["citation"]["start"] <= m["end"] + 1e-6, (m, e))
        self.assertEqual([m["start"] for m in ms], sorted(m["start"] for m in ms))

    def test_emphasis_moment_points_at_the_exact_line(self):
        m = next(m for m in self.ws.key_moments(ALICE, self.lid)["key_moments"] if "exam" in m["reason"])
        self.assertEqual((m["start"], m["slide_index"], m["seek_seconds"]), (16.0, 3, 16.0))
        self.assertIn("Remember this for the exam", m["evidence"][0]["text"])

    def test_summary_sections_quote_only_their_evidence(self):
        sm = self.ws.summary(ALICE, self.lid)
        self.assertEqual([s["title"] for s in sm["sections"]], ["Gradient Descent", "Backpropagation", "Regularization", "Convolutions"])
        for s in sm["sections"]:
            self.assertEqual(s["origin"], "notiv_generated")
            transcript_text = [e["text"] for e in s["evidence"] if e["source_type"] == "transcript"]
            for part in (s["body"] or "").split(" … "):
                self.assertIn(part, transcript_text)                            # every quoted excerpt is linked evidence
            self.assertTrue(any(e["source_type"] == "slide" for e in s["evidence"]))

    def test_generated_items_are_never_indexed_as_lecture_evidence(self):
        n_before = len(self.repo.list_evidence(self.lid))
        self.ws.key_moments(ALICE, self.lid)
        self.assertEqual(len(self.repo.list_evidence(self.lid)), n_before)
        for q in ("Notiv", "first appears", "Professor emphasis"):               # insight wording is not searchable as lecture content
            self.assertEqual(self.ws.search(ALICE, self.lid, q)["results"], [])

    def test_insights_cannot_reference_foreign_evidence(self):
        other = self.env.lecture(ALICE, self.video, self.pdf, title="Other")
        self.env.process(other, segments(*SEGS))
        foreign = self.repo.list_evidence(other)[0]["id"]
        with self.assertRaises(ValueError):
            self.repo.replace_insights(self.lid, [dict(kind="key_moment", title="x", method="t", evidence_ids=[foreign])], "fp")
        self.assertEqual(len(self.ws.key_moments(ALICE, self.lid)["key_moments"]), 6)   # rolled back, original intact

    def test_student_correction_regenerates_insights(self):
        lid = self.env.lecture(ALICE, self.video, self.pdf, title="Corrected")
        self.env.process(lid, segments(*SEGS))
        before = self.ws.summary(ALICE, lid)["sections"]
        self.assertIn("convolution", " ".join(s["body"] or "" for s in before if s["slide_index"] == 4))
        seg = next(s for s in self.repo.viewer_data(lid)["segments"] if s["start"] == 21.0)
        self.repo.set_segment_slide(lid, seg["id"], 1)                           # student: this line belongs to slide 1
        after = self.ws.summary(ALICE, lid)["sections"]
        self.assertNotIn("convolution", " ".join(s["body"] or "" for s in after if s["slide_index"] == 4))
        self.assertIn("convolution", " ".join(s["body"] or "" for s in after if s["slide_index"] == 1))
        self.assertFalse(insights.ensure(self.repo, lid))                        # unchanged evidence -> no needless rewrite

    def test_no_cue_no_emphasis_moment(self):
        lid = self.env.lecture(ALICE, self.video, self.pdf, title="Plain")
        self.env.process(lid, segments(Segment(1, 4, "Gradient descent uses the gradient."), Segment(7, 12, "Layers pass signals forward.")))
        ms = self.ws.key_moments(ALICE, lid)["key_moments"]
        self.assertEqual({m["method"] for m in ms}, {"rule:slide_change"})


# ───────────────────────── search ─────────────────────────
class TestSearch(WsBase):
    def test_transcript_hit_cites_slide_and_timestamp_with_highlights(self):
        r = self.ws.search(ALICE, self.lid, "chain rule")
        self.assertEqual((r["match"], r["count"]), ("all", 1))
        h = r["results"][0]
        self.assertEqual((h["source_type"], h["origin_label"], h["citation"]["label"], h["citation"]["seek_seconds"]),
                         ("transcript", "Professor said", "Slide 2 · 00:07", 7.0))
        self.assertEqual([p["text"] for p in h["snippet"] if p["match"]], ["chain", "rule"])
        self.assertEqual("".join(p["text"] for p in h["snippet"]).replace("…", ""), h["text"])   # highlights never alter text

    def test_slide_hit_cites_slide_deck_and_first_appearance(self):
        r = self.ws.search(ALICE, self.lid, "point 2 about regularization")
        h = r["results"][0]
        self.assertEqual((r["count"], h["source_type"], h["origin_label"], h["citation"]["slide_index"]), (1, "slide", "Slide text", 3))
        self.assertAlmostEqual(h["citation"]["seek_seconds"], 15.0, delta=0.6)
        self.assertEqual(h["citation"]["source"]["type"], "pdf")

    def test_searches_across_transcript_and_slides(self):
        r = self.ws.search(ALICE, self.lid, "convolutions")                       # slide title + (stemmed) transcript "convolution"
        self.assertEqual({h["source_type"] for h in r["results"]}, {"slide", "transcript"})
        self.assertEqual({h["source_type"] for h in self.ws.search(ALICE, self.lid, "convolutions", source_type="slide")["results"]}, {"slide"})

    def test_every_result_position_round_trips_through_locate(self):
        for q in ("gradient", "regularization", "kernel", "learning rate"):
            for h in self.ws.search(ALICE, self.lid, q)["results"]:
                pos = self.ws.locate(ALICE, self.lid, evidence_id=h["evidence_id"])
                self.assertEqual(pos["seek_seconds"], h["citation"]["seek_seconds"])
                self.assertEqual(pos["slide_index"], h["citation"]["slide_index"])

    def test_any_term_fallback_is_flagged(self):
        r = self.ws.search(ALICE, self.lid, "kernel nonexistentword")
        self.assertEqual(r["match"], "any")
        self.assertEqual(r["results"][0]["citation"]["label"], "Slide 4 · 00:21")

    def test_bad_queries(self):
        for q in ("", "   ", "x" * 201):
            with self.assertRaises(ValidationError, msg=q[:10]):
                self.ws.search(ALICE, self.lid, q)
        with self.assertRaises(ValidationError):
            self.ws.search(ALICE, self.lid, "gradient", source_type="key_moment")
        self.assertEqual(self.ws.search(ALICE, self.lid, 'x" OR "y')["results"], [])         # no FTS injection
        self.assertEqual(self.ws.search(ALICE, self.lid, "zzzqqq")["count"], 0)

    def test_text_with_markup_is_returned_as_data_not_html(self):
        lid = self.env.lecture(ALICE, self.video, self.pdf, title="X")
        self.env.process(lid, segments(Segment(1, 4, "Use <script>alert(1)</script> carefully, gradient.")))
        h = self.ws.search(ALICE, lid, "gradient", source_type="transcript")["results"][0]
        self.assertIn("<script>", h["text"])                                     # API returns raw text; the UI renders via textContent only


# ───────────────────────── position / locate ─────────────────────────
class TestLocate(WsBase):
    def test_time_slide_segment_resolve_consistently(self):
        self.assertEqual(self.ws.locate(ALICE, self.lid, t=9.0)["slide_index"], 2)
        self.assertEqual(self.ws.locate(ALICE, self.lid, t=17.0)["slide_index"], 3)
        p = self.ws.locate(ALICE, self.lid, slide_index=4)
        self.assertEqual(p["slide_index"], 4)
        self.assertAlmostEqual(p["seek_seconds"], 20.0, delta=0.6)
        seg = self.ws.transcript(ALICE, self.lid)["segments"][2]
        self.assertEqual(self.ws.locate(ALICE, self.lid, segment_id=seg["segment_id"])["seek_seconds"], 16.0)
        self.assertIsNone(self.ws.locate(ALICE, self.lid, t=5.0)["segment_id"])             # silence between lines
        self.assertAlmostEqual(self.ws.locate(ALICE, self.lid, t=9999)["seek_seconds"], self.repo.get_lecture(self.lid)["duration"], places=3)

    def test_exactly_one_selector_and_bad_ids(self):
        for kw in ({}, {"t": 1, "slide_index": 1}):
            with self.assertRaises(ValidationError):
                self.ws.locate(ALICE, self.lid, **kw)
        with self.assertRaises(ValidationError):
            self.ws.locate(ALICE, self.lid, t=-1)
        for kw in ({"slide_index": 99}, {"segment_id": 10 ** 6}, {"evidence_id": 10 ** 6}):
            with self.assertRaises(NotFoundError):
                self.ws.locate(ALICE, self.lid, **kw)

    def test_manual_correction_is_reflected_in_navigation(self):
        lid = self.env.lecture(ALICE, self.video, self.pdf, title="Manual")
        self.env.process(lid, segments(*SEGS))
        seg = next(s for s in self.ws.transcript(ALICE, lid)["segments"] if s["start"] == 21.0)
        self.repo.set_segment_slide(lid, seg["segment_id"], 2)
        self.assertEqual(self.ws.locate(ALICE, lid, segment_id=seg["segment_id"])["slide_index"], 2)    # manual alignment wins over timeline
        self.assertEqual(self.ws.locate(ALICE, lid, evidence_id=seg["evidence_id"])["slide_index"], 2)
        again = next(s for s in self.ws.transcript(ALICE, lid)["segments"] if s["start"] == 21.0)
        self.assertTrue(again["slide_manually_set"])


# ───────────────────────── authorization ─────────────────────────
class TestAuthorization(WsBase):
    CALLS = [("overview", {}), ("timeline", {}), ("transcript", {}), ("slides", {}), ("evidence", {}), ("key_moments", {}),
             ("summary", {}), ("search", {"q": "gradient"}), ("locate", {"t": 1.0})]

    def test_non_member_gets_not_found_on_every_endpoint(self):
        for name, kw in self.CALLS:
            with self.assertRaises(NotFoundError, msg=name):
                getattr(self.ws, name)(BOB, self.lid, **kw)

    def test_missing_lecture_is_indistinguishable_from_forbidden(self):
        for name, kw in self.CALLS:
            with self.assertRaises(NotFoundError, msg=name) as missing:
                getattr(self.ws, name)(ALICE, 10 ** 6, **kw)
            with self.assertRaises(NotFoundError, msg=name) as forbidden:
                getattr(self.ws, name)(BOB, self.lid, **kw)
            self.assertEqual(str(missing.exception), str(forbidden.exception))

    def test_evidence_of_another_lecture_is_not_addressable_even_for_a_member(self):
        other = self.env.lecture(ALICE, self.video, self.pdf, title="Alice second")
        self.env.process(other, segments(*SEGS))
        foreign = self.repo.list_evidence(other)[0]["id"]
        with self.assertRaises(NotFoundError):
            self.ws.evidence_item(ALICE, self.lid, foreign)                      # right user, wrong lecture
        with self.assertRaises(NotFoundError):
            self.ws.locate(ALICE, self.lid, evidence_id=foreign)
        with self.assertRaises(NotFoundError):
            self.ws.evidence_item(BOB, other, foreign)

    def test_workspace_members_have_access_and_outsiders_still_do_not(self):
        wid = self.env.lectures.create_course(ALICE, "Shared course")
        course = self.repo.get_course(wid)
        lid = self.env.lecture(ALICE, self.video, self.pdf, title="Shared", course_id=wid)
        self.env.process(lid, segments(*SEGS))
        self.repo.add_member(course["workspace_id"], BOB)
        self.assertEqual(self.ws.overview(BOB, lid)["title"], "Shared")
        self.assertEqual(self.ws.search(BOB, lid, "gradient")["count"] >= 1, True)
        with self.assertRaises(NotFoundError):
            self.ws.overview(EVE, lid)

    def test_search_never_returns_another_lectures_evidence(self):
        lid = self.env.lecture(BOB, self.video, self.pdf, title="Bob's")
        self.env.process(lid, segments(Segment(1, 4, "Secret xylophone material.")))
        self.assertEqual(self.ws.search(ALICE, self.lid, "xylophone")["count"], 0)
        self.assertEqual(self.ws.search(BOB, lid, "xylophone")["count"], 1)

    def test_signed_media_urls_only_verify_for_their_own_key(self):
        ov = self.ws.overview(ALICE, self.lid)
        key, qs = ov["media"]["url"][len("/files/"):].split("?")
        q = dict(kv.split("=") for kv in qs.split("&"))
        self.assertTrue(self.env.storage.verify_signed(key, int(q["exp"]), q["sig"]))
        self.assertFalse(self.env.storage.verify_signed(key.replace(".mp4", ".mkv"), int(q["exp"]), q["sig"]))

    def test_deleting_a_lecture_removes_generated_insights(self):
        lid = self.env.lecture(ALICE, self.video, self.pdf, title="To delete")
        self.env.process(lid, segments(*SEGS))
        self.ws.key_moments(ALICE, lid)
        self.env.assets.delete_lecture(ALICE, lid)
        from core.lecture import store
        c = store.conn()
        self.assertEqual(c.execute("SELECT COUNT(*) FROM lecture_insights WHERE lecture_id=?", (lid,)).fetchone()[0], 0)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM lecture_insight_meta WHERE lecture_id=?", (lid,)).fetchone()[0], 0)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM insight_evidence WHERE insight_id NOT IN (SELECT id FROM lecture_insights)").fetchone()[0], 0)


# ───────────────────────── other input modes ─────────────────────────
class TestInputModes(WsBase):
    def test_slides_only_has_no_times_and_says_so(self):
        from pptx import Presentation
        p = Presentation()
        for t, b in [("Gradient Descent", "step size is the learning rate"), ("Overfitting", "model memorizes noise")]:
            s = p.slide_layouts[1]; sl = p.slides.add_slide(s); sl.shapes.title.text = t; sl.placeholders[1].text = b
        deck = os.path.join(self.tmp, "so.pptx"); p.save(deck)
        lid = self.env.lecture(ALICE, None, deck, title="Slides only")
        self.env.process(lid)
        ov = self.ws.overview(ALICE, lid)
        self.assertEqual((ov["media"], ov["mode"]["can_seek"], ov["mode"]["has_transcript"]), (None, False, False))
        self.assertEqual(ov["warnings"][0]["code"], "no_recording")
        h = self.ws.search(ALICE, lid, "memorizes noise")["results"][0]
        self.assertEqual((h["citation"]["label"], h["citation"]["seek_seconds"], h["citation"]["slide_index"]), ("Slide 2", None, 2))   # no invented time
        self.assertEqual(self.ws.locate(ALICE, lid, slide_index=2)["seek_seconds"], None)
        ms = self.ws.key_moments(ALICE, lid)["key_moments"]
        self.assertEqual([(m["slide_index"], m["start"]) for m in ms], [(1, None), (2, None)])
        self.assertEqual(self.ws.timeline(ALICE, lid)["spans"], [])

    def test_audio_only_with_slides_warns_and_leaves_lines_unassigned(self):
        m4a = os.path.join(self.tmp, "a.mp3")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", self.video, "-vn", "-c:a", "libmp3lame", m4a], check=True, timeout=30)
        lid = self.env.lecture(ALICE, m4a, self.pdf, title="Audio")
        self.env.process(lid, segments(*SEGS))
        ov = self.ws.overview(ALICE, lid)
        self.assertEqual((ov["media"]["type"], ov["mode"]["has_audio_only"]), ("audio", True))
        self.assertIn("audio_only_slides", [w["code"] for w in ov["warnings"]])
        self.assertTrue(all(s["slide_index"] is None for s in self.ws.transcript(ALICE, lid)["segments"]))
        self.assertEqual(self.ws.search(ALICE, lid, "kernel")["results"][0]["citation"]["seek_seconds"], 21.0)   # time still citable

    def test_audio_only_no_slides_uses_time_window_summary(self):
        m4a = os.path.join(self.tmp, "b.mp3")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", self.video, "-vn", "-c:a", "libmp3lame", m4a], check=True, timeout=30)
        lid = self.env.lecture(ALICE, m4a, None, title="Audio only")
        self.env.process(lid, segments(*SEGS))
        secs = self.ws.summary(ALICE, lid)["sections"]
        self.assertEqual([s["method"] for s in secs], ["extractive:time_window"])
        self.assertEqual(len(secs[0]["evidence"]), 3)
        self.assertEqual(self.ws.slides(ALICE, lid)["slides"], [])


# ───────────────────────── resilience ─────────────────────────
class TestResilience(WsBase):
    def test_analyze_failure_never_fails_the_lecture_and_is_regenerated_on_read(self):
        real = insights.ensure
        calls = {"n": 0}

        def boom(repo, lid, force=False):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("analysis backend down")
            return real(repo, lid, force)
        insights.ensure = boom
        try:
            lid = self.env.lecture(ALICE, self.video, self.pdf, title="Resilient")
            self.env.process(lid, segments(*SEGS))
            self.assertEqual(self.repo.get_lecture(lid)["status"], "done")
            job = self.repo.list_jobs(lid)[-1]
            st = next(s for s in self.repo.list_stages(job["id"]) if s["stage"] == "ANALYZE")
            self.assertIn("analysis failed", st["output"]["skipped"])
            self.assertEqual(len(self.ws.key_moments(ALICE, lid)["key_moments"]), 6)       # generated lazily on first read
        finally:
            insights.ensure = real

    def test_reprocessing_replaces_insights_without_orphans(self):
        lid = self.env.lecture(ALICE, self.video, self.pdf, title="Twice")
        self.env.process(lid, segments(*SEGS))
        self.env.process(lid, segments(*SEGS))
        self.assertEqual(len(self.repo.list_insights(lid)), 10)                    # 6 key moments + 4 sections, not doubled
        ids = {e["id"] for e in self.repo.list_evidence(lid)}
        for i in self.repo.list_insights(lid):
            self.assertTrue(set(i["evidence_ids"]) <= ids)


if __name__ == "__main__":
    unittest.main()
