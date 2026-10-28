"""OCR fallback for pages without a text layer (scanned PDFs), and how missing page text is surfaced to the user.

The end-to-end OCR tests need the `tesseract` binary and are skipped (reported) without it; the parsing, gating and
warning tests run everywhere."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from core.lecture import health, media, slides
from core.lecture.testkit import Env
from core.lecture.workspace import WorkspaceService

HAS_TESSERACT = shutil.which("tesseract") is not None


class OcrOn(unittest.TestCase):
    """conftest turns OCR off for the rest of the suite; these tests run with the production default (auto)."""
    def setUp(self):
        p = mock.patch.object(slides, "OCR_MODE", "auto")
        p.start()
        self.addCleanup(p.stop)


def make_scanned_pdf(path: str, lines=("Photosynthesis is defined as the process", "by which plants convert light into energy.")):
    """An image-only PDF (no text layer), like a scan."""
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (1600, 1000), "white")
    d = ImageDraw.Draw(im)
    font = ImageFont.load_default(size=48)
    for i, line in enumerate(lines):
        d.text((80, 100 + 80 * i), line, fill="black", font=font)
    im.save(path)
    return path


TSV = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
       "1\t1\t0\t0\t0\t0\t0\t0\t100\t100\t-1\t\n"
       "5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t90\tHello\n"
       "5\t1\t1\t1\t1\t2\t0\t0\t10\t10\t80\tworld\n"
       "5\t1\t1\t1\t2\t1\t0\t0\t10\t10\t70\tSecond\n"
       "5\t1\t1\t1\t2\t2\t0\t0\t10\t10\t-1\t \n")


class TestOcrParsing(unittest.TestCase):
    def test_tsv_lines_and_mean_confidence(self):
        text, conf = slides._parse_tsv(TSV)
        self.assertEqual(text, "Hello world\nSecond")
        self.assertAlmostEqual(conf, 80.0)

    def test_empty_tsv(self):
        self.assertEqual(slides._parse_tsv("level\tconf\ttext\n"), ("", None))


class TestOcrFallback(OcrOn):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        self.pdf = make_scanned_pdf(os.path.join(self.tmp, "scan.pdf"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def out(self):
        return tempfile.mkdtemp(dir=self.tmp)

    @unittest.skipUnless(HAS_TESSERACT, "tesseract not installed (OCR path UNVERIFIED)")
    def test_scanned_page_is_read_with_ocr(self):
        [s] = slides.load_deck(self.pdf, self.out())
        self.assertEqual(s.text_source, "ocr")
        self.assertIn("photosynthesis", s.text.lower())
        self.assertGreaterEqual(s.ocr_confidence, slides.OCR_MIN_CONFIDENCE)
        self.assertEqual(s.warnings, [])

    @unittest.skipUnless(HAS_TESSERACT, "tesseract not installed (OCR path UNVERIFIED)")
    def test_speckled_scan_is_recovered_by_the_despeckle_retry(self):
        import random
        from PIL import Image, ImageDraw, ImageFont, ImageFilter
        # A page of text under heavy speckle. (One isolated line under the same noise stays below the threshold and is
        # correctly discarded: the specks then outnumber the real words.)
        im = Image.new("L", (1700, 2200), 245)
        d, font = ImageDraw.Draw(im), ImageFont.load_default(size=40)
        for i, t in enumerate(["Cellular respiration is the process by which cells",
                               "break down glucose to release energy as ATP.",
                               "Mitochondria are where most respiration happens."]):
            d.text((120, 200 + 70 * i), t, fill=30, font=font)
        im = im.rotate(1.5, fillcolor=245).filter(ImageFilter.GaussianBlur(1.2))
        px, rnd = im.load(), random.Random(1)
        for _ in range(60000):
            px[rnd.randrange(1700), rnd.randrange(2200)] = rnd.choice((0, 255))
        path = os.path.join(self.tmp, "speckled.pdf")
        im.save(path)
        [s] = slides.load_deck(path, self.out())
        self.assertEqual(s.text_source, "ocr")
        self.assertIn("mitochondria", s.text.lower())

    def test_despeckle_retry_keeps_the_more_confident_reading(self):
        calls = []
        def fake(img):
            calls.append(img)
            return ("noise", 20.0) if len(calls) == 1 else ("Clean text", 85.0)
        with mock.patch.object(slides, "_tesseract", fake):
            text, conf = slides._ocr_page(self.pdf, 1, self.out())
        self.assertEqual((text, conf, len(calls)), ("Clean text", 85.0, 2))
        self.assertTrue(calls[1].endswith("-clean.png"))

    def test_confident_first_pass_is_not_retried(self):
        calls = []
        with mock.patch.object(slides, "_tesseract", lambda img: calls.append(img) or ("Good", 90.0)):
            self.assertEqual(slides._ocr_page(self.pdf, 1, self.out()), ("Good", 90.0))
        self.assertEqual(len(calls), 1)

    def test_ocr_off_leaves_page_empty_and_says_why(self):
        with mock.patch.object(slides, "OCR_MODE", "off"):
            [s] = slides.load_deck(self.pdf, self.out())
        self.assertEqual((s.text, s.text_source), ("", "none"))
        self.assertIn("ocr_unavailable", s.warnings)

    def test_low_confidence_ocr_is_discarded(self):
        with mock.patch.object(slides, "ocr_available", lambda: True), \
             mock.patch.object(slides, "_ocr_page", lambda *a: ("sc#ibbl3 w0rds", 22.0)):
            [s] = slides.load_deck(self.pdf, self.out())
        self.assertEqual(s.text, "")
        self.assertIn("ocr_low_confidence", s.warnings)

    def test_ocr_failure_on_a_page_does_not_fail_the_deck(self):
        def boom(*a):
            raise media.MediaTimeout("tesseract timed out after 120s")
        with mock.patch.object(slides, "ocr_available", lambda: True), mock.patch.object(slides, "_ocr_page", boom):
            [s] = slides.load_deck(self.pdf, self.out())
        self.assertIn("ocr_failed", s.warnings)


class TestPipelineAndOverview(OcrOn):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        self.env = Env(self.tmp)
        self.pdf = make_scanned_pdf(os.path.join(self.tmp, "scan.pdf"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def overview(self, lid):
        return WorkspaceService(self.env.repo, self.env.storage).overview(1, lid)

    @unittest.skipUnless(HAS_TESSERACT, "tesseract not installed (OCR path UNVERIFIED)")
    def test_scanned_pdf_becomes_evidence_with_ocr_confidence_and_a_notice(self):
        lid = self.env.lecture(deck=self.pdf)
        self.env.process(lid)
        [ev] = self.env.repo.list_evidence(lid)
        self.assertEqual(ev["source_type"], "slide")
        self.assertIn("photosynthesis", ev["text"].lower())
        self.assertTrue(0.5 <= ev["confidence"] <= 1.0)
        ov = self.overview(lid)
        codes = [w["code"] for w in ov["warnings"]]
        self.assertIn("ocr_pages", codes)
        self.assertNotIn("pages_without_text", codes)
        self.assertEqual((ov["counts"]["ocr_pages"], ov["counts"]["pages_without_text"]), (1, 0))

    def test_unreadable_pages_are_reported_not_silently_ready(self):
        with mock.patch.object(slides, "OCR_MODE", "off"):
            lid = self.env.lecture(deck=self.pdf)
            self.env.process(lid)
        ov = self.overview(lid)
        w = {x["code"]: x for x in ov["warnings"]}
        self.assertIn("no_readable_content", w)
        self.assertEqual(w["pages_without_text"]["pages"], [1])
        self.assertIn("isn't available on this server", w["pages_without_text"]["message"])
        self.assertEqual(ov["counts"]["evidence"], 0)

    def test_text_warning_messages(self):
        info = {"pages_without_text": [2, 3], "ocr_pages": [4], "ocr_unavailable": [], "ocr_failed": [],
                "ocr_low_confidence": [2, 3]}
        w = {x["code"]: x for x in WorkspaceService._text_warnings(info, 5, 3)}
        self.assertNotIn("no_readable_content", w)
        self.assertIn("2 of 5 page(s)", w["pages_without_text"]["message"])
        self.assertIn("handwriting", w["pages_without_text"]["message"])
        self.assertEqual(w["ocr_pages"]["pages"], [4])


class TestReadinessReportsOcr(unittest.TestCase):
    def test_ocr_unavailable_is_reported_but_blocks_no_format(self):
        with mock.patch.object(slides, "OCR_MODE", "off"):
            ps = media.processing_status()
            chk = health.readiness()["checks"]["processing_tools"]
        self.assertFalse(ps["ocr_available"])
        self.assertNotIn("pdf", ps["blocked_formats"])
        self.assertTrue(chk["ok"])
        self.assertIn("OCR unavailable", chk["detail"])


if __name__ == "__main__":
    unittest.main()
