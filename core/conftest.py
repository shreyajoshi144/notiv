"""Test configuration.

1. Makes imports work from any cwd (repo root, backend/, core/ on sys.path;
   pytest.ini sets the same via `pythonpath`).
2. Registers whisper_test_stub.py as `whisper` when openai-whisper isn't
   installed, so collection doesn't crash. The stub cannot transcribe
   (see its docstring); real Whisper is only used by `integration` tests.
3. Legacy meeting/RAG test files whose imports cannot be resolved in this
   checkout are NOT collected — and the skip is reported in the pytest
   summary with the missing module names, never silent.
"""
import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "backend"), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# The shared fixture decks are deliberately image-only (no text layer); the suite's expectations assume they yield no
# slide text. OCR is therefore off by default in tests; test_ocr.py switches it on explicitly.
os.environ.setdefault("NOTIV_OCR", "off")
os.environ.setdefault("NOTIV_VISION", "off")              # never call a real vision model from unit tests
os.environ.setdefault("NOTIV_AI_STUDY", "off")            # ...nor generate AI study material (tests opt in with fakes)

# ── 2. whisper stub ──────────────────────────────────────────────────────
if "whisper" not in sys.modules:
    try:
        import whisper  # noqa: F401  (real package present)
    except ImportError:
        _spec = importlib.util.spec_from_file_location("whisper", os.path.join(_HERE, "whisper_test_stub.py"))
        _stub = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_stub)
        sys.modules["whisper"] = _stub

# ── 3. legacy test files and the modules they need ───────────────────────
_LC = ["langchain_core", "langchain_mistralai", "langchain_chroma", "langchain_community", "langchain_text_splitters"]
LEGACY_REQUIRES = {
    "test_chat_api.py": ["pydantic", *_LC],
    "test_extractor_findings.py": ["transcriber", "langchain_core", "langchain_mistralai", "langchain_text_splitters"],
    "test_job_queue.py": ["core.transcriber", "core.db", "core.summarizer", "utils.audio_processor", "streamlit"],
    "test_rag_engine.py": ["transcriber", "core.transcriber", *_LC],
    "test_vector_store.py": ["transcriber", *_LC],
}


def _available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def legacy_missing() -> dict:
    out = {}
    for fname, reqs in LEGACY_REQUIRES.items():
        missing = [r for r in reqs if not _available(r)]
        if missing:
            out[fname] = missing
    return out


LEGACY_MISSING = legacy_missing()
collect_ignore = list(LEGACY_MISSING)


def pytest_terminal_summary(terminalreporter):
    if LEGACY_MISSING:
        terminalreporter.write_sep("-", "legacy tests NOT collected (missing modules)")
        for f, mods in LEGACY_MISSING.items():
            terminalreporter.write_line(f"  {f}: {', '.join(mods)}")
