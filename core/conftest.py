"""Test configuration.

1. Makes imports work from any cwd (repo root, backend/, core/ on sys.path;
   pytest.ini sets the same via `pythonpath`).
2. Turns off OCR, vision and AI study generation by default, so unit tests never call a real model.
"""
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
