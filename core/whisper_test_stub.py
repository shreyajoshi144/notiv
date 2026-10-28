"""Stand-in for the `whisper` (openai-whisper) module, registered by
conftest.py ONLY when the real package isn't installed.

Why it exists: legacy modules do `import whisper` at import time, which
would otherwise break test *collection* on machines without torch/whisper.

What it is not: a fake transcriber. `load_model` raises, so any test that
reaches real transcription without monkeypatching fails loudly instead of
silently getting made-up text. Unit tests inject transcripts explicitly
(see core/lecture/testkit.segments); real speech-to-text is exercised only
by tests marked `integration` (core/test_integration_whisper.py).
"""
__version__ = "0.0-test-stub"
IS_TEST_STUB = True


class WhisperUnavailable(RuntimeError):
    pass


def load_model(*args, **kwargs):
    raise WhisperUnavailable(
        "openai-whisper is not installed; this is the test stub. Monkeypatch the loader in unit tests, "
        "or install whisper/faster-whisper and run the `integration` tests for real transcription.")


def available_models():
    return []
