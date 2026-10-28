"""Backend reliability & core-workflow regression tests: supported formats, PDF/PPTX rendering, MP3/MP4 processing,
transcript validation, Ask answer quality (AWS question bank), Google/OIDC removal, and the upload -> process -> preview ->
Ask -> refresh -> retry workflow.

Real tools are used everywhere (ffmpeg, poppler, LibreOffice). The ONLY stand-in is the speech recogniser: real Whisper
weights cannot be downloaded in the test environment, so transcripts are explicit test INPUT with known timestamps
(see test_integration_whisper.py for the real-model test, which is skipped when the model is unavailable).
"""
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
import zipfile

from core.lecture import jobs, media, passages, pipeline, retrieval, slides, transcribe, validation
from core.lecture.align import Segment
from core.lecture.ask import NOT_ENOUGH, AskService
from core.lecture.errors import PermanentError, ValidationError
from core.lecture.testkit import Env, segments
from core.lecture.workspace import WorkspaceService

ALICE = 1
FF = ["ffmpeg", "-nostdin", "-y", "-v", "error"]


def ff(*args):
    subprocess.run([*FF, *args], check=True, timeout=120, stdin=subprocess.DEVNULL)


def make_mp3(path, seconds, cover=False):
    ff("-f", "lavfi", "-i", f"sine=frequency=330:sample_rate=44100:duration={seconds}", "-c:a", "libmp3lame", "-b:a", "64k", path)
    if cover:
        jpg = path + ".jpg"
        ff("-f", "lavfi", "-i", "color=c=red:s=64x64:d=1", "-frames:v", "1", jpg)
        out = path + ".cover.mp3"
        ff("-i", path, "-i", jpg, "-map", "0:a", "-map", "1:v", "-c", "copy", "-id3v2_version", "3",
           "-metadata:s:v", "title=cover", "-disposition:v", "attached_pic", out)
        os.replace(out, path)


def make_mp4(path, seconds, audio=True):
    args = ["-f", "lavfi", "-i", f"testsrc=size=320x240:rate=10:duration={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=48000:duration={seconds}", "-c:a", "aac", "-shortest"]
    ff(*args, "-c:v", "libx264", "-pix_fmt", "yuv420p", path)


def make_pdf(path, sizes, rotate=None, edge_text=True):
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(path)
    for i, (w, h) in enumerate(sizes):
        c.setPageSize((w, h))
        c.setFillColorRGB(0.1, 0.2, 0.6)
        c.rect(0, 0, w, h, fill=1)                              # full-bleed background: any crop is visible
        c.setFillColorRGB(1, 1, 1)
        c.setFont("Helvetica-Bold", 24)
        c.drawString(40, h - 60, f"Page {i + 1} title")
        c.setFont("Helvetica", 14)
        c.drawString(40, h - 100, f"Body text on page {i + 1} about topic{i + 1}.")
        if edge_text:
            c.drawString(6, 6, f"EDGE-BOTTOM-{i + 1}")
            c.drawRightString(w - 6, h - 14, f"EDGE-TOP-{i + 1}")
        c.showPage()
    c.save()
    if rotate:
        from pypdf import PdfReader, PdfWriter
        r, w = PdfReader(path), PdfWriter()
        for pg in r.pages:
            w.add_page(pg)
        for idx, deg in rotate.items():
            w.pages[idx].rotate(deg)
        w.write(path)


def make_pptx(path, sizes, hidden=(), with_image=None):
    from pptx import Presentation
    from pptx.util import Emu, Inches
    prs = Presentation()
    for i, (w_in, h_in) in enumerate(sizes[:1]):
        prs.slide_width, prs.slide_height = Inches(w_in), Inches(h_in)
    for i in range(len(sizes)):
        s = prs.slides.add_slide(prs.slide_layouts[5])
        s.shapes.title.text = f"Slide {i + 1} title"
        tb = s.shapes.add_textbox(Inches(0.5), Inches(2), Inches(5), Inches(1))
        tb.text_frame.text = f"Details for slide {i + 1}"
        if with_image and i == 0:
            s.shapes.add_picture(with_image, Inches(1), Inches(3), Inches(2), Inches(2))
        if i in hidden:
            s._element.set("show", "0")
    prs.save(path)


def png_dims(path):
    return slides.png_size(path)


class Tmp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def p(self, name):
        return os.path.join(self.tmp, name)


# ───────────────────────── 1. supported formats ─────────────────────────
class TestSupportedFormats(Tmp):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mp3, cls.mp4 = os.path.join(cls.tmp, "a.mp3"), os.path.join(cls.tmp, "v.mp4")
        make_mp3(cls.mp3, 5)
        make_mp4(cls.mp4, 4)
        cls.pdf = os.path.join(cls.tmp, "d.pdf")
        make_pdf(cls.pdf, [(600, 400)])
        cls.pptx = os.path.join(cls.tmp, "d.pptx")
        make_pptx(cls.pptx, [(10, 7.5)])

    def test_the_four_formats_are_accepted(self):
        self.assertEqual(validation.validate_file(self.mp3, "a.mp3", "audio/mpeg").type, "audio")
        v = validation.validate_file(self.mp4, "v.mp4", "video/mp4")
        self.assertEqual((v.type, v.mime_type), ("video", "video/mp4"))
        self.assertEqual(validation.validate_file(self.pdf, "d.pdf", "application/pdf").type, "pdf")
        self.assertEqual(validation.validate_file(self.pptx, "d.pptx").type, "pptx")
        self.assertAlmostEqual(validation.validate_file(self.mp3, "a.mp3").duration_seconds, 5, delta=0.5)

    def test_everything_else_is_rejected_and_the_message_names_the_supported_formats(self):
        wav, m4a, mkv, png = (self.p(n) for n in ("a.wav", "a.m4a", "v.mkv", "i.png"))
        ff("-i", self.mp3, wav)
        ff("-i", self.mp3, "-c:a", "aac", m4a)
        ff("-i", self.mp4, "-c", "copy", mkv)
        open(png, "wb").write(b"\x89PNG\r\n\x1a\n" + b"\0" * 40)
        for path, name in ((wav, "a.wav"), (m4a, "a.m4a"), (mkv, "v.mkv"), (png, "i.png"), (self.pdf, "d.docx"), (self.mp3, "a.ogg")):
            with self.assertRaises(ValidationError, msg=name) as cm:
                validation.validate_file(path, name)
            self.assertIn("PDF, PPTX, MP3, MP4", str(cm.exception))

    def test_advertised_formats_match_the_validator(self):
        f = validation.supported_formats()
        self.assertEqual(f["extensions"], [".pdf", ".pptx", ".mp3", ".mp4"])
        self.assertEqual(sorted(validation.MEDIA_EXT | validation.EXT["pdf"] | validation.EXT["pptx"]), sorted(f["extensions"]))

    def test_content_must_match_the_extension(self):
        fake = self.p("fake.mp3")
        shutil.copyfile(self.mp4, fake)                       # an MP4 container named .mp3
        with self.assertRaises(ValidationError):
            validation.validate_file(fake, "fake.mp3")
        as_mp4 = self.p("fake.mp4")
        shutil.copyfile(self.mp3, as_mp4)
        with self.assertRaises(ValidationError):
            validation.validate_file(as_mp4, "fake.mp4")

    def test_mp3_with_cover_art_is_audio_not_video(self):
        p = self.p("cover.mp3")
        make_mp3(p, 4, cover=True)
        self.assertFalse(media.probe(p)["has_video"])
        self.assertEqual(validation.validate_file(p, "cover.mp3").type, "audio")

    def test_video_without_audio_is_rejected_clearly(self):
        p = self.p("silent.mp4")
        make_mp4(p, 3, audio=False)
        with self.assertRaises(ValidationError) as cm:
            validation.validate_file(p, "silent.mp4")
        self.assertIn("no audio", str(cm.exception))

    def test_corrupt_empty_and_truncated_files(self):
        empty = self.p("e.pdf")
        open(empty, "wb").close()
        garbage_pdf = self.p("g.pdf")
        open(garbage_pdf, "wb").write(b"%PDF-1.4\nthis is not a real pdf body at all\n%%EOF")
        trunc_mp4 = self.p("t.mp4")
        data = open(self.mp4, "rb").read()
        open(trunc_mp4, "wb").write(data[: len(data) // 3])
        garbage_mp3 = self.p("g.mp3")
        open(garbage_mp3, "wb").write(os.urandom(4096))
        bad_pptx = self.p("b.pptx")
        with zipfile.ZipFile(bad_pptx, "w") as z:
            z.writestr("[Content_Types].xml", "<x/>")
            z.writestr("ppt/presentation.xml", "<x/>")      # a package with no slides
        for path, name in ((empty, "e.pdf"), (garbage_pdf, "g.pdf"), (trunc_mp4, "t.mp4"), (garbage_mp3, "g.mp3"), (bad_pptx, "b.pptx")):
            with self.assertRaises(ValidationError, msg=name):
                validation.validate_file(path, name)

    def test_oversized_file_is_rejected(self):
        old = validation.MAX_BYTES
        validation.MAX_BYTES = 100
        try:
            with self.assertRaises(ValidationError):
                validation.validate_file(self.mp3, "a.mp3")
        finally:
            validation.MAX_BYTES = old


# ───────────────────────── 2. PDF / PPTX rendering ─────────────────────────
class TestRendering(Tmp):
    def test_pdf_every_page_rendered_with_correct_shape_and_nothing_cropped(self):
        pdf = self.p("mixed.pdf")
        sizes = [(960, 540), (612, 792), (842, 595), (400, 400), (300, 900)]            # 16:9, letter, A4 landscape, square, tall
        make_pdf(pdf, sizes)
        deck = slides.load_deck(pdf, self.p("o1"))
        self.assertEqual([s.index for s in deck], [1, 2, 3, 4, 5])
        for s, (w, h) in zip(deck, sizes):
            self.assertAlmostEqual(s.width / s.height, w / h, delta=0.01, msg=f"slide {s.index} aspect")
            self.assertEqual(max(s.width, s.height), slides.RENDER_PX)
            self.assertIn(f"Page {s.index} title", s.text)
        # full-bleed background: if any border were cropped/white the corner pixels would differ from the background colour
        from PIL import Image
        for s in deck:
            im = Image.open(s.image_path).convert("RGB")
            for xy in ((0, 0), (im.width - 1, 0), (0, im.height - 1), (im.width - 1, im.height - 1)):
                r, g, b = im.getpixel(xy)
                self.assertLess(r, 60, f"slide {s.index} corner {xy} is not background")
        self.assertIn("EDGE-BOTTOM-1", deck[0].text)                                      # content at the page edge is extracted too
        self.assertIn("EDGE-TOP-5", deck[4].text)

    def test_pdf_rotated_page_is_displayed_upright(self):
        pdf = self.p("rot.pdf")
        make_pdf(pdf, [(600, 400), (600, 400)], rotate={0: 90})
        deck = slides.load_deck(pdf, self.p("o2"))
        self.assertGreater(deck[1].width, deck[1].height)
        self.assertGreater(deck[0].height, deck[0].width)                                 # the rotated page is portrait on screen

    def test_pdf_many_pages_count_matches_source(self):
        pdf = self.p("many.pdf")
        make_pdf(pdf, [(500, 300)] * (slides.RENDER_CHUNK + 7), edge_text=False)           # spans two render chunks
        deck = slides.load_deck(pdf, self.p("o3"))
        self.assertEqual(len(deck), slides.RENDER_CHUNK + 7)
        self.assertEqual([s.index for s in deck], list(range(1, slides.RENDER_CHUNK + 8)))

    def test_pdf_load_is_idempotent_over_a_dirty_output_dir(self):
        pdf = self.p("idem.pdf")
        make_pdf(pdf, [(500, 300)] * 3)
        out = self.p("o4")
        slides.load_deck(pdf, out)
        open(os.path.join(out, "slide-9.png"), "wb").write(b"stale")                       # leftover from a dead attempt
        self.assertEqual(len(slides.load_deck(pdf, out)), 3)

    def test_pptx_hidden_slides_are_rendered_and_text_stays_aligned(self):
        pp = self.p("hidden.pptx")
        make_pptx(pp, [(10, 7.5)] * 4, hidden={1})
        deck = slides.load_deck(pp, self.p("o5"))
        self.assertEqual(len(deck), 4)                                                     # LibreOffice alone would give 3
        for s in deck:
            self.assertIn(f"Slide {s.index} title", s.text)
            self.assertAlmostEqual(s.width / s.height, 10 / 7.5, delta=0.01)

    def test_pptx_16_9_and_portrait_sizes_and_images(self):
        from PIL import Image
        img = self.p("pic.png")
        Image.new("RGB", (80, 80), (200, 30, 30)).save(img)
        wide, tall = self.p("wide.pptx"), self.p("tall.pptx")
        make_pptx(wide, [(13.333, 7.5)] * 2, with_image=img)
        make_pptx(tall, [(7.5, 13.333)] * 2)
        for path, ratio in ((wide, 13.333 / 7.5), (tall, 7.5 / 13.333)):
            deck = slides.load_deck(path, self.p("o_" + os.path.basename(path)))
            self.assertEqual(len(deck), 2)
            self.assertAlmostEqual(deck[0].width / deck[0].height, ratio, delta=0.01)

    def test_pptx_failures_are_explicit(self):
        bad = self.p("corrupt.pptx")
        open(bad, "wb").write(b"PK\x03\x04 not really a presentation")
        with self.assertRaises(media.MediaError):
            slides.load_deck(bad, self.p("o6"))
        with self.assertRaises(media.MediaError):
            slides.load_deck(self.p("x.doc"), self.p("o7"))

    def test_subprocess_timeout_kills_the_process_group(self):
        with self.assertRaises(media.MediaTimeout):
            media._run(["sh", "-c", "sleep 30 & sleep 30"], timeout=1)


# ───────────────────────── 3. MP3 / MP4 processing ─────────────────────────
class TestMedia(Tmp):
    def test_extract_audio_full_length_for_mp3_and_mp4(self):
        mp3, mp4 = self.p("a.mp3"), self.p("v.mp4")
        make_mp3(mp3, 12)
        make_mp4(mp4, 9)
        for src, dur in ((mp3, 12), (mp4, 9)):
            info = media.probe(src)
            wav = media.extract_audio(src, self.p("out.wav"), expected_duration=info["duration"], format_name=info["format_name"])
            w = media.wav_info(wav)
            self.assertAlmostEqual(w["duration"], dur, delta=0.6)
            self.assertEqual((w["sample_rate"], w["channels"]), (16000, 1))

    def test_different_sample_rates_and_codecs(self):
        for sr, codec in ((22050, "libmp3lame"), (48000, "libmp3lame")):
            p = self.p(f"sr{sr}.mp3")
            ff("-f", "lavfi", "-i", f"sine=frequency=440:sample_rate={sr}:duration=6", "-c:a", codec, p)
            w = media.wav_info(media.extract_audio(p, self.p("o.wav"), expected_duration=6))
            self.assertAlmostEqual(w["duration"], 6, delta=0.5)

    def test_truncated_audio_is_never_returned_as_complete(self):
        p = self.p("trunc.mp3")
        make_mp3(p, 20)
        full = media.probe(p)["duration"]
        data = open(p, "rb").read()
        cut = self.p("cut.mp3")
        open(cut, "wb").write(data[: len(data) // 2])
        # the probe of the cut file would simply report a shorter duration; the check that matters is against the ORIGINAL
        with self.assertRaises(media.MediaError) as cm:
            media.extract_audio(cut, self.p("o2.wav"), expected_duration=full, format_name="mp3")
        self.assertIn("truncated", str(cm.exception))
        self.assertFalse(os.path.exists(self.p("o2.wav")))                                   # no half-written file left behind

    def test_retry_overwrites_a_stale_wav(self):
        p = self.p("r.mp3")
        make_mp3(p, 5)
        open(self.p("r.wav"), "wb").write(b"stale" * 100)
        self.assertAlmostEqual(media.wav_info(media.extract_audio(p, self.p("r.wav"), expected_duration=5))["duration"], 5, delta=0.5)

    def test_missing_binary_is_a_clear_error(self):
        with self.assertRaises(media.MediaError) as cm:
            media._run(["definitely-not-installed-xyz"], timeout=5)
        self.assertIn("not found", str(cm.exception))

    def test_tools_status_reports_dependencies(self):
        st = media.tools_status()
        for k in ("ffmpeg", "ffprobe", "pdftoppm", "soffice"):
            self.assertIn(k, st)

    def test_python_packages_status_reports_pipeline_packages(self):
        st = media.python_packages_status()
        for k in ("faster-whisper", "python-pptx", "opencv-python-headless", "numpy"):
            self.assertIn(k, st)

    def test_missing_package_blocks_its_formats_and_shows_in_readiness(self):
        from unittest import mock
        from core.lecture import health
        real = __import__("importlib.util").util.find_spec
        hide = {"faster_whisper", "pptx"}
        with mock.patch("importlib.util.find_spec", lambda name, *a, **k: None if name in hide else real(name, *a, **k)), \
             mock.patch.object(media, "tools_status", lambda: dict.fromkeys(media.TOOL_FORMATS, True)):
            ps = media.processing_status()
            self.assertEqual(ps["missing_packages"], ["faster-whisper", "python-pptx"])
            self.assertEqual(ps["blocked_formats"], ["pptx", "mp3", "mp4"])
            chk = health.readiness()["checks"]["processing_tools"]
        self.assertTrue(chk["ok"])                                    # informational: an API-only replica may lack them
        self.assertIn("faster-whisper", chk["detail"])
        self.assertIn("cannot process: PPTX, MP3, MP4", chk["detail"])
        self.assertEqual(chk["blocked_formats"], ["pptx", "mp3", "mp4"])

    def test_missing_binary_blocks_its_formats(self):
        from unittest import mock
        with mock.patch.object(media, "tools_status", lambda: {**dict.fromkeys(media.TOOL_FORMATS, True), "soffice": False}), \
             mock.patch.object(media, "python_packages_status", lambda: {p: True for p, _ in media.PYTHON_PACKAGES.values()}):
            ps = media.processing_status()
        self.assertEqual((ps["missing_tools"], ps["missing_packages"], ps["blocked_formats"]), (["soffice"], [], ["pptx"]))


class TestTranscriptValidation(unittest.TestCase):
    def S(self, *t):
        return [Segment(a, b, x) for a, b, x in t]

    def test_clean_transcript_passes_and_is_ordered(self):
        out, rep = transcribe.validate_segments(self.S((5, 7, "two"), (0, 4, "one")), 30)
        self.assertEqual([s.text for s in out], ["one", "two"])
        self.assertEqual(rep["segments"], 2)
        self.assertTrue(all(a.end <= b.start + 1e-9 for a, b in zip(out, out[1:])))

    def test_small_overlap_trimmed_large_overlap_rejected(self):
        out, rep = transcribe.validate_segments(self.S((0, 5, "a"), (4.5, 9, "b")), 30)
        self.assertGreaterEqual(out[1].start, out[0].end)
        with self.assertRaises(transcribe.TranscriptInvalid):
            transcribe.validate_segments(self.S((0, 20, "a"), (5, 9, "b")), 30)

    def test_timestamps_past_the_media_end_are_rejected_but_rounding_is_clamped(self):
        out, rep = transcribe.validate_segments(self.S((0, 10.8, "a")), 10)
        self.assertEqual(out[0].end, 10)
        with self.assertRaises(transcribe.TranscriptInvalid):
            transcribe.validate_segments(self.S((0, 50, "a")), 10)
        with self.assertRaises(transcribe.TranscriptInvalid):
            transcribe.validate_segments(self.S((float("nan"), 3, "a")), 10)

    def test_empty_text_dropped_and_early_end_reported(self):
        out, rep = transcribe.validate_segments(self.S((0, 5, "  "), (1, 3, "hello")), 600)
        self.assertEqual(len(out), 1)
        self.assertIn("transcript_ends_early", rep["warnings"])


class TestPipelineWorkflow(Tmp):
    """upload -> process -> preview -> Ask -> refresh -> retry, on real MP3/MP4 + PDF files."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = Env(cls.tmp)
        cls.repo = cls.env.repo
        cls.ws = WorkspaceService(cls.repo, cls.env.storage)
        cls.mp3, cls.mp4, cls.pdf = (os.path.join(cls.tmp, n) for n in ("talk.mp3", "talk.mp4", "deck.pdf"))
        make_mp3(cls.mp3, 30)
        make_mp4(cls.mp4, 20)
        make_pdf(cls.pdf, [(960, 540)] * 3)

    REF = [Segment(0.5, 4.0, "Welcome to the course on object storage."), Segment(4.5, 9.0, "Buckets hold objects and each object has a key."),
           Segment(10.0, 18.0, "Versioning keeps every revision of an object.")]

    def test_mp3_lecture_timestamps_and_ready(self):
        lid = self.env.lecture(ALICE, self.mp3, None, title="mp3")
        self.env.process(lid, segments(*self.REF))
        self.assertEqual(self.repo.get_lecture(lid)["status"], "done")
        segs = self.ws.transcript(ALICE, lid)["segments"]
        self.assertEqual([round(s["start"], 1) for s in segs], [0.5, 4.5, 10.0])               # reference timestamps preserved
        starts = [s["start"] for s in segs]
        self.assertEqual(starts, sorted(starts))
        asset = self.repo.list_assets(lid)[0]
        self.assertAlmostEqual(asset["duration_seconds"], 30, delta=1)

    def test_mp4_with_pdf_preview_and_counts(self):
        lid = self.env.lecture(ALICE, self.mp4, self.pdf, title="mp4+pdf")
        self.env.process(lid, segments(*self.REF[:2]))
        sl = self.ws.slides(ALICE, lid)["slides"]
        self.assertEqual(len(sl), 3)                                                          # page count == source
        self.assertTrue(all(s["image_url"] for s in sl))
        for s in sl:
            key = self.repo.viewer_data(lid)["slides"][s["slide_index"] - 1]["image_key"]
            with self.env.storage.local_path(key) as path:
                w, h = png_dims(path)
            self.assertAlmostEqual(w / h, 960 / 540, delta=0.01)

    def test_transcript_past_the_end_fails_and_is_not_ready(self):
        lid = self.env.lecture(ALICE, self.mp4, None, title="bad ts")
        with self.assertRaises(transcribe.TranscriptInvalid):
            self.env.process(lid, segments(Segment(0, 500, "impossible")))
        self.assertNotEqual(self.repo.get_lecture(lid)["status"], "done")
        self.assertEqual(self.repo.viewer_data(lid)["segments"], [])                          # nothing partial was stored

    def test_no_speech_without_slides_fails_permanently(self):
        lid = self.env.lecture(ALICE, self.mp3, None, title="silent")
        with self.assertRaises(PermanentError):
            self.env.process(lid, segments())
        self.assertEqual(self.repo.get_lecture(lid)["status"], "failed")

    def test_retry_resumes_without_duplicating(self):
        calls = {"n": 0}

        def flaky(wav, lang, prog, dur):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient decode hiccup")
            return list(self.REF)

        lid = self.env.lecture(ALICE, self.mp3, self.pdf, title="retry")
        js = self.env.jobs(flaky)
        jid = js.enqueue(lid, max_attempts=3)
        self.assertEqual(js.run_next().status, jobs.RETRYING)
        self.assertEqual(js.run_next().status, jobs.COMPLETED)
        self.assertEqual(len(self.ws.slides(ALICE, lid)["slides"]), 3)
        self.assertEqual(len(self.ws.transcript(ALICE, lid)["segments"]), 3)
        # re-running the whole pipeline again replaces, never duplicates
        self.env.process(lid, segments(*self.REF))
        self.assertEqual(len(self.ws.transcript(ALICE, lid)["segments"]), 3)
        self.assertEqual(len(self.ws.slides(ALICE, lid)["slides"]), 3)

    def test_original_source_files_are_preserved(self):
        lid = self.env.lecture(ALICE, self.mp3, self.pdf, title="orig")
        self.env.process(lid, segments(*self.REF))
        for a in self.repo.list_assets(lid):
            with self.env.storage.local_path(a["storage_key"]) as path:
                self.assertEqual(validation.sha256_file(path), a["checksum"])


# ───────────────────────── 4. Ask Notiv answer quality ─────────────────────────
QUESTIONS = [
    ("Which AWS service provides object storage?", "Amazon S3", "Amazon EBS", "Amazon RDS", "Amazon SQS"),
    ("Which AWS service is a managed relational database?", "Amazon RDS", "Amazon S3", "AWS Lambda", "Amazon SNS"),
    ("Which AWS service runs code without servers?", "AWS Lambda", "Amazon EC2", "Amazon S3", "AWS Glue"),
    ("What is AWS?", "A cloud platform", "A database engine", "A programming language", "A hardware vendor"),
    ("Which AWS Marketplace feature lets customers buy third-party software?", "Private offers", "Spot fleets", "Edge locations", "Snowball"),
    ("What does AWS IAM manage?", "Users and permissions", "Billing alarms", "DNS records", "Disk encryption"),
    ("Which AWS service delivers content from edge locations?", "Amazon CloudFront", "Amazon Route 53", "AWS Shield", "Amazon EFS"),
    ("Which AWS service provides a managed message queue?", "Amazon SQS", "Amazon SNS", "Amazon MQ", "AWS Step Functions"),
    ("Which AWS tool estimates monthly costs?", "AWS Pricing Calculator", "AWS Config", "AWS Trusted Advisor", "AWS X-Ray"),
    ("Which AWS service hosts DNS?", "Amazon Route 53", "Amazon VPC", "AWS Direct Connect", "AWS WAF"),
    ("Which AWS service provides virtual servers?", "Amazon EC2", "AWS Fargate", "Amazon ECR", "AWS Batch"),
    ("Which AWS service caches data in memory?", "Amazon ElastiCache", "Amazon DynamoDB", "Amazon Redshift", "Amazon Athena"),
    ("Which AWS support plan includes a technical account manager?", "Enterprise", "Basic", "Developer", "Business"),
    ("Which AWS service audits API calls?", "AWS CloudTrail", "Amazon CloudWatch", "AWS Config", "Amazon Inspector"),
    ("Which AWS service encrypts keys in hardware?", "AWS CloudHSM", "AWS KMS", "AWS Secrets Manager", "AWS Shield"),
]


def make_question_bank(path, with_definition=False):
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(path, pagesize=(612, 792))
    per_page = 5
    items = list(QUESTIONS)
    for pg in range(0, len(items), per_page):
        c.setFont("Helvetica-Bold", 16)
        c.drawString(40, 750, "AWS Cloud Practitioner Question Bank")
        c.setFont("Helvetica", 10)
        y = 715
        for n, (q, *opts) in enumerate(items[pg:pg + per_page], start=pg + 1):
            c.drawString(40, y, f"{n}. {q}")
            y -= 14
            for letter, o in zip("ABCD", opts):
                c.drawString(60, y, f"{letter}. {o}")
                y -= 13
            y -= 12
        c.showPage()
    if with_definition:
        c.setFont("Helvetica-Bold", 16)
        c.drawString(40, 750, "What is AWS?")
        c.setFont("Helvetica", 11)
        c.drawString(40, 720, "AWS is Amazon's cloud computing platform that offers on-demand compute, storage and databases over the internet.")
        c.showPage()
    c.save()


class TestAskQuality(Tmp):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = Env(cls.tmp)
        cls.repo = cls.env.repo
        cls.bank = os.path.join(cls.tmp, "bank.pdf")
        make_question_bank(cls.bank)
        cls.lid = cls.env.lecture(ALICE, None, cls.bank, title="AWS question bank")
        cls.env.process(cls.lid, segments())
        cls.bank2 = os.path.join(cls.tmp, "bank2.pdf")
        make_question_bank(cls.bank2, with_definition=True)
        cls.lid2 = cls.env.lecture(ALICE, None, cls.bank2, title="AWS bank + intro")
        cls.env.process(cls.lid2, segments())

    def svc(self, llm=None):
        return AskService(self.repo, self.env.storage, llm=llm)

    def test_pages_are_split_into_question_passages(self):
        ev = [e for e in self.repo.list_evidence(self.lid) if e["source_type"] == "slide"]
        self.assertEqual(len(ev), 3)
        units = passages.PassageIndex(ev).units
        self.assertGreaterEqual(len(units), 12)                                                # ~5 questions per page
        self.assertTrue(all(len(u.text) <= passages.MAX_PASSAGE_CHARS * 1.6 for u in units))

    def test_what_is_aws_does_not_dump_unrelated_questions(self):
        r = self.svc()._answer(self.lid, "What is AWS?", [])
        self.assertFalse(r["grounded"])                                                         # the bank never defines AWS
        self.assertEqual(r["reason"], "no_definition")
        self.assertIn("doesn't explain what AWS is", r["answer"])
        self.assertIn("exam-style questions", r["answer"])
        self.assertNotIn("Marketplace", r["answer"])                                            # no unrelated question quoted back
        for i in r["cited"]:
            self.assertEqual(self.repo.get_evidence(i)["lecture_id"], self.lid)                 # citations are real evidence

    def test_what_is_aws_with_a_real_definition_leads_with_it(self):
        r = self.svc()._answer(self.lid2, "What is AWS?", [])
        self.assertTrue(r["grounded"], r)
        self.assertTrue(r["answer"].startswith("In short"), r["answer"])
        self.assertIn("cloud computing platform", r["answer"])
        self.assertNotIn("Marketplace", r["answer"])
        ev = self.repo.get_evidence(r["cited"][0])
        self.assertEqual(ev["slide_index"], 4)                                                  # provenance: the definition page

    def test_specific_question_returns_the_matching_passage_not_the_page(self):
        r = self.svc()._answer(self.lid, "Which AWS service provides object storage?", [])
        self.assertTrue(r["grounded"], r)
        self.assertIn("object storage", r["answer"].lower())
        self.assertNotIn("Marketplace", r["answer"])
        self.assertNotIn("Route 53", r["answer"])
        q = list(r["quotes"].values())[0]
        self.assertLess(len(q), 400)                                                            # a passage, not a whole page

    def test_marketplace_question_finds_the_marketplace_item(self):
        r = self.svc()._answer(self.lid, "Which feature lets customers buy third-party software in AWS Marketplace?", [])
        self.assertTrue(r["grounded"])
        self.assertIn("Marketplace", r["answer"])

    def test_unanswerable_question_says_so(self):
        for q in ("How do I configure Kubernetes ingress?", "Who won the 2018 football world cup?"):
            r = self.svc()._answer(self.lid, q, [])
            self.assertFalse(r["grounded"], q)
            self.assertEqual(r["answer"], NOT_ENOUGH)

    def test_model_is_not_called_when_the_source_cannot_answer(self):
        calls = []
        r = self.svc(llm=lambda s, u: calls.append(1) or "x")._answer(self.lid, "What is AWS?", [])
        self.assertEqual(calls, [])
        self.assertTrue(r["meta"]["general_knowledge_offer"])                                   # but the UI may offer the labelled follow-up

    def test_general_knowledge_is_separate_labelled_and_uncited(self):
        gk = lambda system, user: "AWS is Amazon's cloud platform. [E5]"
        svc = self.svc(llm=gk)
        m = svc.chat(ALICE, self.lid, "What is AWS?")
        self.assertFalse(m["grounded"])
        self.assertTrue(m["general_knowledge_offer"])
        g = svc.general_knowledge(ALICE, self.lid, m["message_id"])
        self.assertEqual((g["origin"], g["citations"]), ("general_knowledge", []))
        self.assertIn("not from your uploaded material", g["origin_label"])
        self.assertNotIn("[E5]", g["content"])                                                  # forged citation markers stripped
        self.assertEqual(svc.general_knowledge(ALICE, self.lid, m["message_id"])["message_id"], g["message_id"])   # idempotent
        hist = self.svc(llm=gk).history(ALICE, self.lid)["messages"]                            # "refresh"
        self.assertEqual([x.get("origin") for x in hist if x["role"] == "assistant"], ["notiv_generated", "general_knowledge"])

    def test_general_knowledge_refused_for_grounded_answers_and_without_a_model(self):
        svc = self.svc(llm=lambda s, u: "x")
        m = svc.chat(ALICE, self.lid2, "What is AWS?")
        self.assertTrue(m["grounded"])
        with self.assertRaises(ValidationError):
            svc.general_knowledge(ALICE, self.lid2, m["message_id"])
        with self.assertRaises(ValidationError):
            self.svc().general_knowledge(ALICE, self.lid2, m["message_id"])

    def test_other_users_cannot_request_general_knowledge(self):
        svc = self.svc(llm=lambda s, u: "text")
        m = svc.chat(ALICE, self.lid, "What is AWS?")
        from core.lecture.errors import NotFoundError
        with self.assertRaises(NotFoundError):
            svc.general_knowledge(2, self.lid, m["message_id"])

    def test_conflicting_figures_are_flagged(self):
        ev = [{"id": 1, "context": False, "text": "The free tier allows 5 GB of storage."},
              {"id": 2, "context": False, "text": "The free tier allows 20 GB of storage."}]
        from core.lecture.ask import detect_conflict
        terms = retrieval.content_terms("free tier storage")
        c = detect_conflict(terms, ev)
        self.assertEqual(sorted(c["ids"]), [1, 2])
        self.assertIsNone(detect_conflict(terms, [ev[0], dict(ev[0], id=3)]))

    def test_retrieval_failure_is_explicit(self):
        svc = self.svc()
        orig = retrieval.retrieve
        retrieval.retrieve = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            r = svc._answer(self.lid, "What is S3?", [])
        finally:
            retrieval.retrieve = orig
        self.assertEqual((r["grounded"], r["reason"]), (False, "retrieval_error"))
        self.assertNotIn("boom", r["answer"])

    def test_question_analysis(self):
        a = passages.analyze_question
        self.assertEqual((a("What is AWS?").kind, a("What is AWS?").subject), ("definition", "AWS"))
        self.assertEqual(a("what does S3 stand for").subject, "S3")
        self.assertEqual(a("Tell me about the shared responsibility model").subject, "shared responsibility model")
        self.assertEqual(a("What is the difference between EC2 and S3?").kind, "compare")
        self.assertEqual(a("How do I create a bucket?").kind, "other")


# ───────────────────────── 5. authentication ─────────────────────────
class TestGoogleRemoved(unittest.TestCase):
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def read(self, *p):
        return open(os.path.join(self.ROOT, *p), encoding="utf-8").read()

    def test_no_google_or_oidc_in_the_frontend(self):
        ui = os.path.join(self.ROOT, "backend", "server", "static")
        for dirpath, _, files in os.walk(ui):
            for f in files:
                if f.endswith((".js", ".html", ".css")):
                    low = open(os.path.join(dirpath, f), encoding="utf-8").read().lower()
                    self.assertNotIn("google", low, f)
                    self.assertNotIn("oidc", low, f)

    def test_provider_code_and_routes_are_gone(self):
        self.assertFalse(os.path.exists(os.path.join(self.ROOT, "backend", "server", "oidc.py")))
        src = self.read("backend", "server", "api", "routes_auth.py")
        self.assertNotIn('"/auth/login"', src)
        self.assertNotIn('"/auth/callback"', src)

    def test_password_auth_is_untouched(self):
        src = self.read("backend", "server", "api", "routes_password_auth.py")
        for needle in ("/auth/register", "/auth/password", "csrf"):
            self.assertIn(needle, src.lower() if needle == "csrf" else src)


if __name__ == "__main__":
    unittest.main()
