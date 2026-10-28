"""REAL speech-to-text integration test (faster-whisper + model weights).

Skipped unless all of these hold:
  * faster-whisper is installed and the model can load (NOTIV_WHISPER_MODEL, default "small")
  * NOTIV_E2E_SPEECH points at a real speech recording (any audio/video file)
  * NOTIV_E2E_EXPECT is a comma-separated list of words that must appear in the transcript
Optionally NOTIV_E2E_DECK points at the matching PDF/PPTX to exercise the full pipeline.

    NOTIV_E2E_SPEECH=lecture.mp4 NOTIV_E2E_EXPECT=gradient,learning pytest -m integration
"""
import os
import shutil
import tempfile
import unittest

try:
    import pytest
    integration = pytest.mark.integration
except ImportError:                                   # unittest-only environments
    def integration(cls):
        return cls

SPEECH = os.getenv("NOTIV_E2E_SPEECH")
EXPECT = [w.strip().lower() for w in os.getenv("NOTIV_E2E_EXPECT", "").split(",") if w.strip()]


def _whisper_ok() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


@integration
@unittest.skipUnless(SPEECH and os.path.exists(SPEECH or "") and EXPECT, "set NOTIV_E2E_SPEECH and NOTIV_E2E_EXPECT")
@unittest.skipUnless(_whisper_ok(), "faster-whisper not installed")
class TestRealWhisper(unittest.TestCase):
    def test_full_pipeline_with_real_transcription(self):
        from core.lecture.testkit import Env
        tmp = tempfile.mkdtemp()
        try:
            env = Env(tmp)
            deck = os.getenv("NOTIV_E2E_DECK")
            lid = env.lecture(1, SPEECH, deck if deck and os.path.exists(deck) else None)
            env.process(lid)                                         # default transcriber = real faster-whisper
            d = env.repo.viewer_data(lid)
            text = " ".join(s["text"] for s in d["segments"]).lower()
            self.assertTrue(d["segments"], "no transcript produced")
            for w in EXPECT:
                self.assertIn(w, text)
            self.assertTrue(all(s["end"] > s["start"] >= 0 for s in d["segments"]))
            self.assertEqual(env.repo.get_lecture(lid)["status"], "done")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
