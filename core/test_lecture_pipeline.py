"""Lecture engine tests (FFmpeg, PDF/PPTX rendering, slide matching, alignment,
search, viewer, corrections, grounded Q&A) running through the real staged
pipeline on a real encoded video + PDF built by make_test_lecture.py.

NOT covered here: Whisper itself (needs faster-whisper + weights). Tests pass
explicit Segment objects as *test inputs* via testkit.segments(); the default
real-transcriber path is checked to fail loudly rather than invent text.
"""
from core.lecture.errors import ValidationError
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import wave

from core.lecture import align, corrections, media, qa, slide_match, slides, viewer
from core.lecture.align import Segment
from core.lecture.errors import PermanentError
from core.lecture.make_test_lecture import make_lecture
from core.lecture.testkit import Env, segments

DURATIONS = [6, 9, 5, 7]


class LectureBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.video, cls.pdf, cls.truth = make_lecture(os.path.join(cls.tmp, "fx"), DURATIONS)
        cls.env = Env(cls.tmp)
        cls.repo = cls.env.repo

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _timeline(self, video):
        deck = slides.load_deck(self.pdf, os.path.join(self.tmp, "sl_" + os.path.basename(video)))
        m = slide_match.SlideMatcher([s.image_path for s in deck])
        dur = media.probe(video)["duration"]
        frames = media.extract_frames(video, os.path.join(self.tmp, "fr_" + os.path.basename(video)), 2.0)
        samples = [(t, m.match(f)[0]) for t, f in frames]
        probe = os.path.join(self.tmp, "p.jpg")
        return slide_match.build_timeline(
            samples, dur, lambda t: m.match(media.extract_frame_at(video, t, probe))[0])


class TestMedia(LectureBase):
    def test_audio_is_16k_mono_and_full_length(self):
        wav = media.extract_audio(self.video, os.path.join(self.tmp, "a.wav"))
        with wave.open(wav) as w:
            self.assertEqual((w.getframerate(), w.getnchannels()), (16000, 1))
            self.assertAlmostEqual(w.getnframes() / 16000, sum(DURATIONS), delta=0.5)

    def test_bad_input_raises(self):
        with self.assertRaises(media.MediaError):
            media.probe(os.path.join(self.tmp, "nope.mp4"))


class TestSlides(LectureBase):
    def test_pdf_renders_one_image_per_page(self):
        deck = slides.load_deck(self.pdf, os.path.join(self.tmp, "deck_t"))
        self.assertEqual([s.index for s in deck], [1, 2, 3, 4])
        self.assertTrue(all(os.path.getsize(s.image_path) > 1000 for s in deck))

    def test_unsupported_deck_type(self):
        with self.assertRaises(media.MediaError):
            slides.load_deck(os.path.join(self.tmp, "x.key"), self.tmp)


class TestSlideTimeline(LectureBase):
    def _assert_matches_truth(self, tl):
        self.assertEqual([e.slide_index for e in tl], [1, 2, 3, 4])
        for e, t in zip(tl[1:], self.truth):
            self.assertAlmostEqual(e.start, t, delta=0.6, msg=f"boundary {e} vs truth {t}")

    def test_recovers_slide_changes_from_real_video(self):
        self._assert_matches_truth(self._timeline(self.video))

    def test_robust_to_speaker_camera_overlay(self):
        out = os.path.join(self.tmp, "pip.mp4")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", self.video, "-vf",
                        "drawbox=x=720:y=330:w=220:h=140:color=0x808080:t=fill",
                        "-c:v", "libx264", "-crf", "30", "-c:a", "copy", out], check=True, timeout=30)
        self._assert_matches_truth(self._timeline(out))

    def test_short_noise_run_is_absorbed(self):
        s = [(0, 1), (2, 1), (4, 3), (6, 1), (8, 1), (10, 2), (12, 2), (14, 2)]
        tl = slide_match.build_timeline(s, 16.0)
        self.assertEqual([e.slide_index for e in tl], [1, 2])
        self.assertEqual(tl[0].end, 10)


class TestAlign(unittest.TestCase):
    TL = [slide_match.TimelineEntry(1, 0, 10), slide_match.TimelineEntry(2, 10, 20),
          slide_match.TimelineEntry(None, 20, 30)]

    def test_assigns_by_max_overlap_and_flags_straddle(self):
        segs = [Segment(1, 4, "a"), Segment(8, 13, "b"), Segment(9, 10.5, "c"), Segment(22, 25, "d"), Segment(40, 41, "e")]
        r = align.align_segments(segs, self.TL)
        self.assertEqual([g["slide_index"] for g in r], [1, 2, 1, None, None])
        self.assertEqual([g["straddles"] for g in r], [False, True, True, False, False])

    def test_low_overlap_lowers_segment_confidence(self):
        tl = [slide_match.TimelineEntry(1, 0, 10, 0.9), slide_match.TimelineEntry(2, 10, 20, 0.9)]
        g = align.align_segments([Segment(8, 14, "x")], tl)[0]
        self.assertEqual(g["slide_index"], 2)
        self.assertAlmostEqual(g["confidence"], round(4 / 6 * 0.9, 4), places=3)


SEGS = [Segment(0.5, 4.0, "Today we cover gradient descent and the learning rate."),
        Segment(7.0, 12.0, "Backpropagation applies the chain rule through every layer."),
        Segment(16.0, 19.0, "Regularization penalizes large weights to reduce overfitting."),
        Segment(21.0, 26.0, "A convolution slides a kernel over the image.")]


class TestEndToEnd(LectureBase):
    def test_video_plus_slides_align_search_viewer(self):
        lid = self.env.lecture(7, self.video, self.pdf)
        self.env.process(lid, segments(*SEGS))
        lec = self.repo.get_lecture(lid)
        self.assertEqual(lec["status"], "done", lec["error"])
        self.assertAlmostEqual(lec["duration"], sum(DURATIONS), delta=0.5)

        d = self.repo.viewer_data(lid)
        self.assertEqual([s["slide_index"] for s in d["segments"]], [1, 2, 3, 4])
        self.assertEqual(len(d["slides"]), 4)
        self.assertTrue(all(s["image_key"].startswith(f"lectures/{lid}/derived/slides/") for s in d["slides"]))

        hits = self.repo.search(lid, "chain rule")
        self.assertEqual(len(hits), 1)
        self.assertEqual((hits[0]["kind"], hits[0]["slide_index"], hits[0]["start"]), ("transcript", 2, 7.0))
        self.assertEqual(self.repo.search(lid, 'x" OR "y'), [])
        self.assertEqual(self.repo.search(lid, ""), [])

        out = viewer.export_html(self.repo, self.env.storage, lid, os.path.join(self.tmp, "v.html"), "lecture.mp4")
        with open(out, encoding="utf-8") as f:
            page = f.read()
        self.assertIn("Backpropagation applies the chain rule", page)
        self.assertIn("data:image/png;base64,", page)

    def test_real_transcriber_unavailable_fails_loudly(self):
        try:
            import faster_whisper  # noqa: F401
            self.skipTest("faster-whisper installed; default path would really transcribe")
        except ImportError:
            pass
        lid = self.env.lecture(1, self.video, self.pdf)
        with self.assertRaises(PermanentError):
            self.env.process(lid)                                # default = real faster-whisper
        lec = self.repo.get_lecture(lid)
        self.assertEqual(lec["status"], "failed")
        self.assertIn("faster-whisper", lec["error"])
        self.assertEqual(self.repo.viewer_data(lid)["segments"], [])

    def test_audio_less_video_is_rejected(self):
        silent = os.path.join(self.tmp, "silent.mp4")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", self.video, "-an", "-c:v", "copy", silent], check=True, timeout=30)
        with self.assertRaises(ValidationError) as cm:              # rejected at UPLOAD now, not deep in processing
            self.env.lecture(1, silent, self.pdf)
        self.assertIn("no audio", str(cm.exception))


class TestInputModes(LectureBase):
    S = [Segment(1.0, 4.0, "Gradient descent moves parameters against the gradient."),
         Segment(7.0, 12.0, "The learning rate sets the step size.")]

    def _m4a(self):
        p = os.path.join(self.tmp, "a.mp3")
        if not os.path.exists(p):
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", self.video, "-vn", "-c:a", "libmp3lame", p], check=True, timeout=30)
        return p

    def test_video_only(self):
        lid = self.env.lecture(1, self.video)
        self.env.process(lid, segments(*self.S))
        d = self.repo.viewer_data(lid)
        self.assertEqual((len(d["slides"]), len(d["timeline"])), (0, 0))
        self.assertEqual([s["slide_index"] for s in d["segments"]], [None, None])    # not guessed

    def test_audio_only(self):
        lid = self.env.lecture(1, self._m4a())
        self.assertEqual(self.repo.list_assets(lid)[0]["type"], "audio")
        self.env.process(lid, segments(*self.S))
        self.assertEqual(len(self.repo.viewer_data(lid)["segments"]), 2)

    def test_audio_plus_deck_has_slides_but_no_alignment(self):
        lid = self.env.lecture(1, self._m4a(), self.pdf)
        self.env.process(lid, segments(*self.S))
        d = self.repo.viewer_data(lid)
        self.assertEqual(len(d["slides"]), 4)
        self.assertEqual(d["timeline"], [])
        self.assertTrue(all(s["slide_index"] is None for s in d["segments"]))

    def test_slides_only_indexes_slide_text(self):
        from pptx import Presentation
        p = Presentation()
        for t, b in [("Gradient Descent", "step size is the learning rate"), ("Overfitting", "model memorizes noise")]:
            s = p.slides.add_slide(p.slide_layouts[1]); s.shapes.title.text = t; s.placeholders[1].text = b
        deck = os.path.join(self.tmp, "text.pptx"); p.save(deck)
        lid = self.env.lecture(1, None, deck)
        self.env.process(lid)
        self.assertEqual(self.repo.get_lecture(lid)["status"], "done", self.repo.get_lecture(lid)["error"])
        hits = self.repo.search(lid, "memorizes noise")
        self.assertEqual([(h["kind"], h["slide_index"]) for h in hits], [("slide", 2)])
        self.assertIsNone(hits[0]["start"])

    def test_nothing_uploaded_fails_permanently(self):
        lid = self.env.lecture(1)
        with self.assertRaises(PermanentError):
            self.env.process(lid, segments())
        self.assertEqual(self.repo.get_lecture(lid)["status"], "failed")


class TestCorrections(LectureBase):
    def _lecture(self, segs):
        lid = self.env.lecture(1, self.video, self.pdf)
        self.env.process(lid, segments(*segs))
        return lid

    def test_text_edit_updates_search(self):
        lid = self._lecture([Segment(1, 3, "gradiant dessent is iterative")])
        seg = self.repo.viewer_data(lid)["segments"][0]
        self.assertEqual(self.repo.search(lid, "gradient"), [])
        self.repo.update_segment_text(lid, seg["id"], "gradient descent is iterative")
        self.assertEqual(len(self.repo.search(lid, "gradient")), 1)
        self.assertEqual(self.repo.search(lid, "gradiant"), [])
        with self.assertRaises(ValueError):
            self.repo.update_segment_text(lid, seg["id"], "   ")

    def test_manual_slide_move_survives_boundary_edit(self):
        lid = self._lecture([Segment(1, 4, "a"), Segment(7, 12, "b")])
        segs = self.repo.viewer_data(lid)["segments"]
        self.assertEqual([s["slide_index"] for s in segs], [1, 2])
        self.repo.set_segment_slide(lid, segs[1]["id"], 4)
        with self.assertRaises(ValueError):
            self.repo.set_segment_slide(lid, segs[0]["id"], 99)
        n = corrections.set_slide_boundaries(self.repo, lid, [{"slide_index": 1, "start": 0, "end": 9},
                                                              {"slide_index": 2, "start": 9, "end": 27}])
        self.assertEqual(n, 1)
        after = self.repo.viewer_data(lid)
        self.assertEqual([s["slide_index"] for s in after["segments"]], [1, 4])
        self.assertEqual(after["segments"][1]["manual_slide"], 1)
        self.assertEqual([e["confidence"] for e in after["timeline"]], [1.0, 1.0])
        self.assertEqual(self.repo.get_evidence(after["segments"][1]["evidence_id"])["slide_index"], 4)

    def test_bad_boundaries_rejected(self):
        lid = self._lecture([Segment(1, 4, "a")])
        for bad in ([{"slide_index": 1, "start": 5, "end": 5}],
                    [{"slide_index": 1, "start": 0, "end": 8}, {"slide_index": 2, "start": 6, "end": 9}],
                    [{"slide_index": 9, "start": 0, "end": 8}]):
            with self.assertRaises(ValueError):
                corrections.set_slide_boundaries(self.repo, lid, bad)


class TestGroundedQA(LectureBase):
    """The LLM below is a TEST DOUBLE standing in for Mistral so retrieval +
    citation verification can be checked deterministically. It says nothing
    about answer quality from a real model."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        e = cls.env
        cls.c = e.lectures.create_course(1, "ML")
        cls.l1 = e.lecture(1, cls.video, cls.pdf, course_id=cls.c)
        e.process(cls.l1, segments(Segment(1, 4, "Gradient descent updates parameters using the negative gradient."),
                                   Segment(7, 12, "The learning rate controls how large each update step is.")))
        cls.l2 = e.lecture(1, cls.video, cls.pdf, course_id=cls.c)
        e.process(cls.l2, segments(Segment(16, 19, "Overfitting happens when a model memorizes training noise.")))
        cls.other = e.lecture(2, cls.video, cls.pdf)
        e.process(cls.other, segments(Segment(1, 3, "secret learning rate trivia")))

    def _eid(self, lid, needle):
        return next(s["evidence_id"] for s in self.repo.viewer_data(lid)["segments"] if needle in s["text"])

    def test_grounded_answer_returns_real_citation_fields(self):
        eid = self._eid(self.l1, "learning rate")
        seen = {}
        def llm(system, user):
            seen["user"] = user
            return f"It sets the update step size [E{eid}].\nFollow-ups:\n- What if it is too large?\n- What if it is too small?"
        r = qa.ask(self.repo, [self.l1], "Why does the learning rate matter?", llm=llm)
        self.assertTrue(r["grounded"])
        self.assertEqual(r["follow_ups"], ["What if it is too large?", "What if it is too small?"])
        c = r["citations"][0]
        self.assertEqual((c["evidence_id"], c["slide_index"], c["start"]), (eid, 2, 7.0))
        self.assertIn(f"[E{eid}]", seen["user"])

    def test_fabricated_citation_dropped_and_ungrounded_answer_rejected(self):
        eid = self._eid(self.l1, "learning rate")
        r = qa.ask(self.repo, [self.l1], "learning rate step", llm=lambda s, u: f"Yes [E{eid}] and also [E99999].")
        self.assertEqual([c["evidence_id"] for c in r["citations"]], [eid])
        self.assertEqual(r["dropped_citations"], [99999])
        r2 = qa.ask(self.repo, [self.l1], "learning rate step", llm=lambda s, u: "The learning rate is great, trust me.")
        self.assertEqual((r2["answer"], r2["grounded"], r2["citations"]), (qa.NOT_ENOUGH, False, []))

    def test_no_evidence_never_calls_llm(self):
        def boom(*a):
            raise AssertionError("LLM must not be called")
        self.assertEqual(qa.ask(self.repo, [self.l1], "quantum chromodynamics lagrangian", llm=boom)["answer"], qa.NOT_ENOUGH)

    def test_scope_lecture_vs_course_vs_other_user(self):
        llm = lambda s, u: f"Found [E{int(re.findall(r'[[]E(\d+)[]]', u)[0])}]."
        self.assertEqual(qa.ask(self.repo, [self.l1], "overfitting memorizes noise", llm=llm)["answer"], qa.NOT_ENOUGH)
        both = qa.ask(self.repo, self.repo.course_lecture_ids(self.c), "overfitting memorizes noise", llm=llm)
        self.assertEqual(both["citations"][0]["lecture_id"], self.l2)
        leak = qa.ask(self.repo, self.repo.course_lecture_ids(self.c), "secret trivia", llm=llm)
        self.assertEqual(leak["answer"], qa.NOT_ENOUGH)


if __name__ == "__main__":
    unittest.main()
