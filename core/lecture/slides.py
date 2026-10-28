"""Slide deck loading: PDF or PPTX -> per-slide image + extracted text.

Pipeline: (PPTX -> PDF via LibreOffice) -> pdfinfo (exact page count + per-page size/rotation) -> pdftoppm (one PNG per
page, long edge = RENDER_PX, aspect ratio preserved, no cropping) -> verification -> text per page.

Verification is part of loading, not an afterthought; a deck is returned ONLY if
  * the number of rendered images equals the number of pages in the source (and, for PPTX, the number of slides in the
    .pptx itself — hidden slides included, which LibreOffice silently drops unless asked),
  * every image is a readable PNG whose aspect ratio matches the page's displayed (rotation-aware) aspect ratio,
  * every image is numbered 1..N exactly once.
Anything else raises MediaError naming the problem; a half-rendered deck is never returned.
"""
import os
import re
import shutil
import struct
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional

from .media import MediaError, MediaTimeout, _run

RENDER_PX = int(os.getenv("NOTIV_SLIDE_RENDER_PX", "1600"))      # long edge of each rendered page image, in pixels
RENDER_CHUNK = 40                                                 # pages per pdftoppm call (bounds memory and time)
ASPECT_TOLERANCE = 0.02
MAX_RENDER_PAGES = int(os.getenv("NOTIV_MAX_PDF_PAGES", "2000"))
# OCR fallback (Tesseract) for pages that have NO text layer (scanned / photographed / image-only slides).
OCR_MODE = os.getenv("NOTIV_OCR", "auto")                         # auto (use tesseract when installed) | off
OCR_LANG = os.getenv("NOTIV_OCR_LANG", "eng")                     # tesseract language codes, e.g. "eng+deu"
OCR_DPI = int(os.getenv("NOTIV_OCR_DPI", "300"))
OCR_MIN_CONFIDENCE = float(os.getenv("NOTIV_OCR_MIN_CONFIDENCE", "50"))   # mean word confidence (0-100) to keep OCR text
OCR_PAGE_TIMEOUT = int(os.getenv("NOTIV_OCR_PAGE_TIMEOUT", "120"))
# Vision transcription (an image-capable answer model reads the rendered page) for handwritten / scanned pages and for
# pages whose embedded text layer is garbled. Preferred over Tesseract, which cannot read handwriting.
VISION_MODE = os.getenv("NOTIV_VISION", "auto")                  # auto (use a configured vision provider) | off
VISION_WORKERS = int(os.getenv("NOTIV_VISION_WORKERS", "2"))
VISION_PX = int(os.getenv("NOTIV_VISION_PX", "1200"))            # long edge sent to the model (image tokens scale with area)
VISION_MAX_WAIT = float(os.getenv("NOTIV_VISION_MAX_WAIT_SECONDS", "900"))   # per page: wait out rate limits / capacity

_PNG_NAME = re.compile(r"^slide-0*(\d+)\.png$")


@dataclass
class Slide:
    index: int       # 1-based
    image_path: str
    text: str
    width: int = 0   # rendered image size in pixels
    height: int = 0
    warnings: List[str] = field(default_factory=list)
    text_source: str = "pdf_text"            # pdf_text | pptx | vision | ocr | none
    ocr_confidence: Optional[float] = None   # mean Tesseract word confidence (0-100) when text_source == "ocr"
    quality: Optional[dict] = None           # text_quality() of the ORIGINAL extracted text (None when there was none)
    transcribed_by: Optional[str] = None     # "provider/model" when text_source == "vision"
    vision_error: Optional[str] = None       # why the vision model could not read it (llm reason codes)


@dataclass
class PageInfo:
    index: int
    width_pt: float            # as DISPLAYED (rotation already applied)
    height_pt: float


# ── PDF inspection ───────────────────────────────────────────────────────
def _pdf_page_count(pdf: str) -> int:
    out = _run(["pdfinfo", pdf], timeout=60).stdout.decode(errors="replace")
    for line in out.splitlines():
        if line.startswith("Pages:"):
            return int(line.split()[1])
    raise MediaError("could not determine PDF page count")


_SIZE = re.compile(r"^Page\s+(\d+)\s+size:\s+([\d.]+)\s+x\s+([\d.]+)\s+pts")
_ROT = re.compile(r"^Page\s+(\d+)\s+rot:\s+(\d+)")


def _pdf_pages(pdf: str, n: int) -> List[PageInfo]:
    """Per-page displayed size. pdfinfo reports the UNROTATED size plus /Rotate; the page is shown swapped for 90/270."""
    out = _run(["pdfinfo", "-f", "1", "-l", str(n), pdf], timeout=120).stdout.decode(errors="replace")
    sizes, rots = {}, {}
    for line in out.splitlines():
        m = _SIZE.match(line)
        if m:
            sizes[int(m.group(1))] = (float(m.group(2)), float(m.group(3)))
        m = _ROT.match(line)
        if m:
            rots[int(m.group(1))] = int(m.group(2)) % 360
    pages = []
    for i in range(1, n + 1):
        if i not in sizes or min(sizes[i]) <= 0:
            raise MediaError(f"page {i} has no usable size (damaged PDF)")
        w, h = sizes[i]
        if rots.get(i, 0) in (90, 270):
            w, h = h, w
        pages.append(PageInfo(i, w, h))
    return pages


def png_size(path: str) -> Optional[tuple]:
    try:
        with open(path, "rb") as f:
            head = f.read(24)
    except OSError:
        return None
    if len(head) < 24 or not head.startswith(b"\x89PNG\r\n\x1a\n") or head[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", head[16:24])
    return (w, h) if w > 0 and h > 0 else None


# ── PPTX -> PDF ──────────────────────────────────────────────────────────
# LibreOffice DROPS hidden slides from a PDF export unless told otherwise, which silently shifts every later slide
# (slide N's text would then be attached to slide N-1's picture). Always export them.
_PDF_FILTER = 'pdf:impress_pdf_Export:{"ExportHiddenSlides":{"type":"boolean","value":"true"}}'


def _pptx_slide_count(pptx: str) -> int:
    from pptx import Presentation
    return len(Presentation(pptx).slides)


def _soffice_timeout(slides: int) -> int:
    return int(min(1800, max(180, 120 + 8 * slides)))


def _pptx_to_pdf(pptx: str, out_dir: str, slides: Optional[int] = None) -> str:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise MediaError("PPTX needs LibreOffice (soffice) to render slides; "
                         "install it or upload the deck as PDF")
    pdf = os.path.join(out_dir, os.path.splitext(os.path.basename(pptx))[0] + ".pdf")
    last: Optional[Exception] = None
    for attempt in (1, 2):                      # LibreOffice's first start in a fresh profile occasionally fails; retry once
        if os.path.exists(pdf):
            os.remove(pdf)
        with tempfile.TemporaryDirectory() as profile:
            try:
                _run([soffice, f"-env:UserInstallation=file://{profile}", "--headless", "--norestore", "--convert-to",
                      _PDF_FILTER, "--outdir", out_dir, pptx], timeout=_soffice_timeout(slides or 0))
            except MediaTimeout:
                raise                            # a timeout is not retried here: the job layer owns retry/backoff
            except MediaError as e:
                last = e
                continue
        if os.path.exists(pdf) and os.path.getsize(pdf) > 0:
            return pdf
        last = MediaError("LibreOffice produced no PDF from the PPTX")
    raise last or MediaError("LibreOffice produced no PDF from the PPTX")


def _shape_texts(shape) -> List[str]:
    parts: List[str] = []
    if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
        parts.append(shape.text_frame.text)
    if getattr(shape, "has_table", False) and shape.has_table:
        for row in shape.table.rows:
            parts.append(" ".join(c.text for c in row.cells))
    if hasattr(shape, "shapes"):                             # group shape: recurse
        for sub in shape.shapes:
            parts.extend(_shape_texts(sub))
    return parts


def _pptx_texts(pptx: str) -> List[str]:
    from pptx import Presentation
    texts = []
    for slide in Presentation(pptx).slides:
        parts: List[str] = []
        for shape in slide.shapes:
            parts.extend(_shape_texts(shape))
        texts.append("\n".join(p for p in parts if p.strip()))
    return texts


# ── rendering ────────────────────────────────────────────────────────────
def _render(pdf: str, n: int, out_dir: str) -> None:
    for f in os.listdir(out_dir):                            # idempotent: never keep images from an earlier attempt
        if _PNG_NAME.match(f):
            os.remove(os.path.join(out_dir, f))
    timeout = int(min(3600, max(120, 60 + 6 * min(RENDER_CHUNK, n))))
    for first in range(1, n + 1, RENDER_CHUNK):
        last = min(n, first + RENDER_CHUNK - 1)
        _run(["pdftoppm", "-f", str(first), "-l", str(last), "-scale-to", str(RENDER_PX), "-png", pdf,
              os.path.join(out_dir, "slide")], timeout=timeout)


def _text_per_page(pdf: str, n: int) -> List[str]:
    out = _run(["pdftotext", "-enc", "UTF-8", pdf, "-"], timeout=max(60, 2 * n)).stdout.decode("utf-8", errors="replace")
    pages = out.split("\f")
    if pages and pages[-1].strip() == "":
        pages = pages[:-1]
    if len(pages) == n:
        return [p.strip() for p in pages]
    return [_run(["pdftotext", "-enc", "UTF-8", "-f", str(i + 1), "-l", str(i + 1), pdf, "-"],      # form feeds were ambiguous
                 timeout=60).stdout.decode("utf-8", errors="replace").strip() for i in range(n)]


# ── text reflow ──────────────────────────────────────────────────────────
# pdftotext / OCR break prose at the page margin, so "…plants convert light into" / "chemical energy." arrive as two
# lines and every line-based consumer (knowledge units, Ask quotes, search snippets) sees half a sentence. reflow()
# re-joins a line with the next one ONLY when the line evidently ran into the margin mid-sentence; slide titles,
# bullets and short lines are left exactly as they are.
_BULLET = re.compile(r"^(?:[-•▪◦*‣–—]|\d{1,3}[.)]|[A-Za-z][.)])\s")
_TERMINAL = re.compile(r"[.!?:;]['\"”’)\]]*$")
_CONTINUES = re.compile(r"(?:[,(/&–-]|\b(?:a|an|the|and|or|of|to|in|on|by|for|from|with|as|at|is|are|was|were|be|"
                        r"which|that|who|whose|than|into|its|their|this|these|such|not|but|if|when|where))$", re.I)
REFLOW_MIN_LINE = 50          # below this longest-line width a page is slide-like: only the lowercase rule applies
REFLOW_MIN_WORDS = 5          # ...and then only after a line of at least this many words (titles are shorter)


def _wrapped(prev: str, cur: str, width: int) -> bool:
    if not (cur and prev) or _BULLET.match(cur) or _TERMINAL.search(prev):
        return False
    if cur[0].islower() and len(prev.split()) >= REFLOW_MIN_WORDS:      # mid-sentence break, at any page width
        return True
    return width >= REFLOW_MIN_LINE and len(prev) >= max(30, 0.6 * width) and \
        (cur[0].isdigit() or cur[0] in "([\"'“‘" or bool(_CONTINUES.search(prev)) or len(prev) >= 0.85 * width)


def reflow(text: str) -> str:
    lines = (text or "").split("\n")
    width = max((len(l.strip()) for l in lines), default=0)
    out: List[str] = []
    joined = False
    for raw in lines:
        cur = raw.strip()
        prev = out[-1] if out else ""
        if _wrapped(prev, cur, width):
            hyphen = prev.endswith("-") and len(prev) > 1 and prev[-2].isalpha() and cur[0].islower()
            out[-1] = prev + ("" if hyphen else " ") + cur        # a broken hyphenated word keeps its hyphen ("self-aware")
            joined = True
        else:
            out.append(cur)
    return "\n".join(out) if joined else text              # nothing wrapped: the extracted text is kept byte-for-byte


# ── extraction quality ───────────────────────────────────────────────────
# A non-empty text layer is not trusted automatically. Handwriting apps and bad scanners embed "recognised" text that
# scatters single words across the page ("Wortdof", "S9Lvs.NoS÷", one word per line with blank lines between); quoting
# it as evidence produces junk concepts and summaries. Measured on such a 39-page notes PDF: every garbled page had
# >=35 lines, >=65% of them one word or less, and >=42% blank lines interleaved; real slides, bullet lists and prose
# never combine the three (lists are short, prose has long lines, slides have no interleaved blanks).
_Q_WORD = re.compile(r"^[A-Za-z][A-Za-z'’-]*[A-Za-z]$|^(?:a|A|I)$")
_Q_VOWEL = re.compile(r"[aeiouyAEIOUY]")
_Q_ODD = re.compile("[÷¥€£§¶©®™µ\ufffd]")
_Q_BULLET = re.compile(r"^\s*(?:[-•▪◦*‣–—]|\d{1,3}[.)]|[A-Za-z][.)])\s+")
QUALITY_MIN_LINES = 12


def text_quality(text: str) -> dict:
    """{"ok", "reasons", metrics...}. ok=False means the text cannot be relied on as evidence."""
    raw = (text or "").split("\n")
    lines = [_Q_BULLET.sub("", ln).strip() for ln in raw if ln.strip()]
    toks = [w.strip(".,:;()[]\"'!?") for ln in lines for w in ln.split()]
    toks = [w for w in toks if w]
    if not toks:
        return {"ok": False, "reasons": ["empty"], "lines": 0, "tokens": 0}
    wordlike = sum(1 for w in toks if _Q_WORD.match(w) and _Q_VOWEL.search(w)) / len(toks)
    frag = sum(1 for ln in lines if len(ln.split()) <= 1) / len(lines)
    blank = sum(1 for ln in raw if not ln.strip()) / len(raw)
    odd = len(_Q_ODD.findall(text)) / max(1, len(text))
    reasons = []
    if len(lines) >= QUALITY_MIN_LINES and frag >= 0.6 and blank >= 0.3:
        reasons.append("scattered_words")                # one word per line, blank lines between: a broken text layer
    if len(toks) >= 20 and wordlike < 0.45 and (frag >= 0.5 or odd >= 0.005):
        reasons.append("non_words")
    if len(toks) >= 10 and odd >= 0.02:
        reasons.append("garbage_symbols")
    return {"ok": not reasons, "reasons": reasons, "lines": len(lines), "tokens": len(toks),
            "wordlike": round(wordlike, 2), "fragmented": round(frag, 2), "blank": round(blank, 2), "odd": round(odd, 3)}


# ── vision transcription ─────────────────────────────────────────────────
VISION_SYSTEM = ("You transcribe one page of study material (slides, typed or handwritten notes) exactly. Output only "
                 "the text on the page in reading order, one line per written line; keep headings, numbering and bullets. "
                 "Do not summarise, explain, correct or add anything. Write [illegible] for words you cannot read. For "
                 "diagrams, write each labelled link on its own line as [diagram: A -> B (label)]. If the page has no "
                 "readable text, output exactly: [no readable text]")
VISION_USER = "Transcribe this page."


def _clean_vision(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`").split("\n", 1)[-1] if "\n" in t else ""
    lines = [ln.rstrip() for ln in t.split("\n")]
    if not lines or all(ln.strip() in ("", "[no readable text]", "[illegible]") for ln in lines):
        return ""
    return "\n".join(lines).strip()


def _vision_png(path: str) -> bytes:
    """The rendered page, downscaled to VISION_PX on the long edge."""
    import cv2
    im = cv2.imread(path)
    if im is None:
        with open(path, "rb") as f:
            return f.read()
    h, w = im.shape[:2]
    scale = VISION_PX / max(h, w)
    if scale < 1:
        im = cv2.resize(im, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".png", im)
    return buf.tobytes() if ok else open(path, "rb").read()


def _vision_page(vision, s: "Slide") -> tuple:
    """(text, "provider/model") or raises. Rate limits and provider over-capacity are waited out (batch work)."""
    from .llm import call_with_backoff
    png = _vision_png(s.image_path)
    done = call_with_backoff(lambda: vision.see(VISION_SYSTEM, VISION_USER, png), VISION_MAX_WAIT)
    return _clean_vision(done.text), f"{done.provider}/{done.model}"


def _vision_recover(vision, slides: List["Slide"]) -> None:
    """Re-reads pages in place with an image-capable model. Failures leave the page for the next fallback."""
    from concurrent.futures import ThreadPoolExecutor
    if not slides:
        return
    with ThreadPoolExecutor(max_workers=max(1, VISION_WORKERS)) as ex:
        results = list(ex.map(lambda s: _try(lambda: _vision_page(vision, s)), slides))
    for s, (res, err) in zip(slides, results):
        if err is not None:
            s.warnings.append("vision_failed")
            fails = getattr(err, "failures", None)
            s.vision_error = ", ".join(f"{p}={r}" for p, r in fails) if fails else type(err).__name__
            continue
        text, by = res
        if text and text_quality(text)["ok"]:
            s.text, s.text_source, s.transcribed_by = text, "vision", by
        else:
            s.warnings.append("vision_unreadable")


def _try(fn):
    try:
        return fn(), None
    except Exception as e:                                   # noqa: BLE001 — reported per page, never fails the deck
        return None, e


# ── OCR fallback ─────────────────────────────────────────────────────────
def ocr_available() -> bool:
    return OCR_MODE != "off" and shutil.which("tesseract") is not None


def _parse_tsv(tsv: str) -> tuple:
    """Tesseract TSV -> (text with one line per recognised line, mean word confidence or None)."""
    lines, confs = {}, []
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5":                 # level 5 = word
            continue
        word = cols[11].strip()
        try:
            conf = float(cols[10])
        except ValueError:
            continue
        if not word or conf < 0:
            continue
        lines.setdefault((int(cols[2]), int(cols[3]), int(cols[4])), []).append(word)
        confs.append(conf)
    text = "\n".join(" ".join(ws) for _, ws in sorted(lines.items()))
    return text, (sum(confs) / len(confs) if confs else None)


def _tesseract(img: str) -> tuple:
    return _parse_tsv(_run(["tesseract", img, "-", "-l", OCR_LANG, "--psm", "3", "tsv"],
                           timeout=OCR_PAGE_TIMEOUT).stdout.decode("utf-8", errors="replace"))


def _despeckle(src: str, dst: str) -> bool:
    """Median filter against scanner speckle, which otherwise drowns Tesseract in noise. False if OpenCV can't read it."""
    import cv2
    im = cv2.imread(src, cv2.IMREAD_GRAYSCALE)
    return im is not None and bool(cv2.imwrite(dst, cv2.medianBlur(im, 5)))


def _ocr_page(pdf: str, page: int, work_dir: str) -> tuple:
    """(text, confidence) for one page, rendered at OCR_DPI in grayscale; a below-threshold result is retried once on a
    despeckled image and the more confident reading wins. Raises MediaError/MediaTimeout."""
    base = os.path.join(work_dir, f"ocr-{page}")
    _run(["pdftoppm", "-f", str(page), "-l", str(page), "-r", str(OCR_DPI), "-gray", "-png", "-singlefile", pdf, base],
         timeout=OCR_PAGE_TIMEOUT)
    img, clean = base + ".png", base + "-clean.png"
    try:
        best = _tesseract(img)
        if (best[1] or 0) < OCR_MIN_CONFIDENCE and _despeckle(img, clean):
            retry = _tesseract(clean)
            if (retry[1] or 0) > (best[1] or 0):
                best = retry
    finally:
        for f in (img, clean):
            if os.path.exists(f):
                os.remove(f)
    return best


def _needs_recovery(s: "Slide") -> bool:
    return s.text_source in ("none", "garbled")


def _ocr_fallback(pdf: str, slides: List[Slide], work_dir: str) -> None:
    """Fills in text for pages with NO text layer (typed scans), in place. A page OCR cannot read stays empty and says why;
    one bad page never fails the deck. Pages whose layer was garbled are not OCR'd: that layer comes from handwriting
    recognition, and Tesseract on handwriting produced confident-looking junk on the System Design notes ("fay ofl of __y |
    Hash funtion") that was then summarised as if it were the source. Only a vision model may recover those pages."""
    empty = [s for s in slides if s.text_source == "none"]
    if not empty:
        return
    if not ocr_available():
        for s in empty:
            s.warnings.append("ocr_unavailable")
        return
    for s in empty:
        try:
            text, conf = _ocr_page(pdf, s.index, work_dir)
        except (MediaError, MediaTimeout):
            s.warnings.append("ocr_failed")
            continue
        if not text.strip():
            continue
        if conf is None or conf < OCR_MIN_CONFIDENCE or not text_quality(text)["ok"]:
            s.warnings.append("ocr_low_confidence")              # likely handwriting / poor scan: not stored as evidence
            continue
        s.text, s.text_source, s.ocr_confidence = reflow(text), "ocr", round(conf, 1)


def _recover(pdf: str, slides: List[Slide], work_dir: str, vision=None) -> None:
    """Pages with no text or a garbled text layer: vision model first (reads handwriting), then Tesseract. Whatever is
    still unreadable is EXCLUDED (empty text) and flagged, never kept as evidence."""
    if vision and VISION_MODE != "off":
        _vision_recover(vision, [s for s in slides if _needs_recovery(s)])
    _ocr_fallback(pdf, slides, work_dir)
    for s in slides:
        if s.text_source == "garbled":                       # nothing could re-read it
            s.text, s.text_source = "", "none"
            s.warnings.append("unreadable_text_excluded")
        if s.text_source in ("vision", "ocr") and "no_extractable_text" in s.warnings:
            s.warnings.remove("no_extractable_text")


def load_deck(deck_path: str, out_dir: str, dpi: Optional[int] = None, vision=None) -> List[Slide]:
    """`dpi` is accepted for backward compatibility and ignored: pages are rendered to a fixed long edge
    (NOTIV_SLIDE_RENDER_PX), which keeps every page legible regardless of its physical size. `vision` is an LLMChain of
    image-capable providers used to re-read handwritten / scanned / garbled pages (None = Tesseract only)."""
    os.makedirs(out_dir, exist_ok=True)
    ext = os.path.splitext(deck_path)[1].lower()
    pptx_texts = None
    expected_slides = None
    if ext == ".pdf":
        pdf = deck_path
    elif ext == ".pptx":
        try:
            pptx_texts = _pptx_texts(deck_path)
            expected_slides = len(pptx_texts)
        except Exception as e:                              # noqa: BLE001 — python-pptx rejects a corrupt/odd package
            raise MediaError(f"the PowerPoint file could not be read: {type(e).__name__}")
        if expected_slides < 1:
            raise MediaError("the presentation contains no slides")
        pdf = _pptx_to_pdf(deck_path, out_dir, expected_slides)
    else:
        raise MediaError(f"unsupported slide deck type '{ext}' (use .pdf or .pptx)")

    n = _pdf_page_count(pdf)
    if n < 1:
        raise MediaError("the document has no pages")
    if n > MAX_RENDER_PAGES:
        raise MediaError(f"the document has {n} pages; the limit is {MAX_RENDER_PAGES}")
    if expected_slides is not None and n != expected_slides:
        raise MediaError(f"LibreOffice rendered {n} page(s) for a {expected_slides}-slide presentation; "
                         f"refusing to continue with misaligned slides")
    pages = _pdf_pages(pdf, n)
    _render(pdf, n, out_dir)

    found = {}
    for f in os.listdir(out_dir):
        m = _PNG_NAME.match(f)
        if m:
            found[int(m.group(1))] = os.path.join(out_dir, f)
    missing = [i for i in range(1, n + 1) if i not in found]
    if missing or len(found) != n:
        raise MediaError(f"expected {n} slide images, rendered {len(found) - len(missing)}"
                         + (f" (missing page(s): {missing[:10]})" if missing else ""))

    texts = _text_per_page(pdf, n)
    slides: List[Slide] = []
    for i in range(1, n + 1):
        path, page = found[i], pages[i - 1]
        size = png_size(path)
        if size is None:
            raise MediaError(f"slide {i} was not rendered to a readable image")
        w, h = size
        want = page.width_pt / page.height_pt
        if abs((w / h) - want) / want > ASPECT_TOLERANCE:
            raise MediaError(f"slide {i} was rendered with the wrong shape ({w}x{h}px for a {page.width_pt:.0f}x"
                             f"{page.height_pt:.0f}pt page); refusing to store a cropped or rotated image")
        text, source = reflow(texts[i - 1]), "pdf_text"
        if pptx_texts is not None and pptx_texts[i - 1].strip() and text_quality(pptx_texts[i - 1])["ok"]:
            text, source = pptx_texts[i - 1], "pptx"         # exact shape text; the PDF text is the fallback (charts, SmartArt)
        quality = text_quality(text) if text.strip() else None
        warn = [] if text.strip() else ["no_extractable_text"]
        if quality and not quality["ok"]:
            warn.append("low_quality_text")
            source = "garbled"                               # kept only until recovery decides; never stored as is
        slides.append(Slide(index=i, image_path=path, text=text, width=w, height=h, warnings=warn,
                            text_source=source if text.strip() else "none", quality=quality))
    _recover(pdf, slides, out_dir, vision)
    return slides
