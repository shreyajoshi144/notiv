"""Provenance: what the viewer is told about WHERE a piece of text came from.

  professor_said    transcript evidence — speech from the recording
  slide_text        slide evidence — text extracted from the uploaded deck
  notiv_generated   anything Notiv derived (key moments, summary sections,
                    future notes/answers) — never presented as lecture content

`lecture evidence` (the first two) is quotable source material; Notiv-generated
items must always link back to evidence rather than stand alone.
"""
ORIGINS = {
    "transcript": ("professor_said", "Professor said"),
    "slide": ("slide_text", "Slide text"),
}
GENERATED = ("notiv_generated", "Notiv analysis")


def origin_of(source_type: str) -> tuple:
    return ORIGINS.get(source_type, GENERATED)


def is_lecture_evidence(source_type: str) -> bool:
    return source_type in ORIGINS


def mmss(t) -> str:
    t = max(0, int(t))
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
