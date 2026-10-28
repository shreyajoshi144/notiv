"""Upload validation. Exactly FOUR formats are supported and tested: PDF, PPTX, MP3 and MP4. The filename is only a hint
for the extension allow-list; the file's CONTENT decides what it is:
  mp3/mp4 -> ffprobe must parse it (real container/codec check), it must contain a decodable AUDIO track and report a
             duration; video vs audio is decided by whether a (non-cover-art) video stream exists
  pdf     -> %PDF- header
  pptx    -> a real zip containing [Content_Types].xml and ppt/ parts, with a cap on total uncompressed size
             (zip-bomb guard)
Declared MIME type (if any) must be plausible for the detected type. Anything else is rejected with a message that
names the supported formats; the list is also published by supported_formats() for the UI / API.
"""
import hashlib
import os
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from typing import Optional

from . import media
from .errors import ValidationError

MAX_BYTES = int(os.getenv("NOTIV_MAX_UPLOAD_MB", "2048")) * 1024 * 1024
MAX_DURATION_SEC = float(os.getenv("NOTIV_MAX_DURATION_HOURS", "8")) * 3600
MAX_UNCOMPRESSED_BYTES = int(os.getenv("NOTIV_MAX_PPTX_UNCOMPRESSED_MB", "1024")) * 1024 * 1024

MAX_PPTX_ENTRIES = int(os.getenv("NOTIV_MAX_PPTX_ENTRIES", "5000"))
MAX_ZIP_RATIO = int(os.getenv("NOTIV_MAX_ZIP_RATIO", "200"))            # per-entry uncompressed/compressed (zip bomb)
MAX_PDF_BYTES = int(os.getenv("NOTIV_MAX_PDF_MB", "200")) * 1024 * 1024
MAX_PDF_PAGES = int(os.getenv("NOTIV_MAX_PDF_PAGES", "2000"))
# ffprobe format_name tokens we accept PER EXTENSION; anything else (hls, concat, sdp, image2, lavfi, ...) is rejected.
# An .mp4 is probed as "mov,mp4,m4a,3gp,3g2,mj2"; an .mp3 as "mp3".
MEDIA_FORMATS = {".mp3": {"mp3"}, ".mp4": {"mp4"}}
PDF_ACTIVE = (b"/javascript", b"/js", b"/launch", b"/embeddedfile", b"/richmedia", b"/submitform", b"/importdata", b"/gotor")
MEDIA_EXT = {".mp3", ".mp4"}
EXT = {"pdf": {".pdf"}, "pptx": {".pptx"}}
SUPPORTED_EXTENSIONS = (".pdf", ".pptx", ".mp3", ".mp4")
SUPPORTED_LABEL = "PDF, PPTX, MP3, MP4"


def supported_formats() -> dict:
    """What the UI / API may advertise. Every entry is covered by the regression suite."""
    return {"extensions": list(SUPPORTED_EXTENSIONS), "label": SUPPORTED_LABEL,
            "accept": ",".join(SUPPORTED_EXTENSIONS),
            "formats": [{"ext": ".pdf", "kind": "slides", "mime": "application/pdf"},
                        {"ext": ".pptx", "kind": "slides", "mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation"},
                        {"ext": ".mp3", "kind": "audio", "mime": "audio/mpeg"},
                        {"ext": ".mp4", "kind": "video", "mime": "video/mp4"}],
            "max_upload_mb": MAX_BYTES // (1024 * 1024), "max_pdf_mb": MAX_PDF_BYTES // (1024 * 1024),
            "max_pdf_pages": MAX_PDF_PAGES, "max_duration_hours": MAX_DURATION_SEC / 3600}

GENERIC_MIME = {"application/octet-stream", "binary/octet-stream", ""}
PPTX_MIME = {"application/vnd.openxmlformats-officedocument.presentationml.presentation", "application/zip",
             "application/x-zip-compressed"}
MP3_MIME = {"audio/mpeg", "audio/mp3", "audio/x-mpeg", "audio/mpeg3", "audio/x-mpeg-3"}
MP4_MIME = {"video/mp4", "audio/mp4", "application/mp4", "video/x-m4v"}


@dataclass
class ValidatedFile:
    type: str                 # video | audio | pdf | pptx
    ext: str
    mime_type: str
    size_bytes: int
    checksum: str             # sha256 hex
    duration_seconds: Optional[float] = None


def safe_filename(name: str) -> str:
    """Display-only filename (storage keys never use it): basename for BOTH separators, NFC, no control chars, no
    leading dots, <=255 chars, never empty."""
    n = unicodedata.normalize("NFC", (name or "").replace("\\", "/").split("/")[-1])
    n = "".join(ch for ch in n if ch.isprintable() and ch not in '<>:"|?*').strip().lstrip(".")
    return (n[:255] or "upload")


_PDF_HEX = re.compile(rb"#([0-9A-Fa-f]{2})")
_PDF_PAGE = re.compile(rb"/Type\s*/Page(?![A-Za-z])")


def _check_pdf(path: str, size: int) -> None:
    if size > MAX_PDF_BYTES:
        raise ValidationError(f"PDF exceeds the {MAX_PDF_BYTES // (1024 * 1024)} MB limit")
    with open(path, "rb") as f:
        data = f.read()
    low = _PDF_HEX.sub(lambda m: bytes([int(m.group(1), 16)]), data).lower()     # defeat /J#61vaScript name escapes
    # scan object dictionaries, not stream bodies (random compressed bytes would false-positive on short tokens).
    # Limitation: active content hidden inside compressed object streams is NOT detected by this scan.
    low = re.sub(rb"stream\r?\n.*?endstream", b"stream endstream", low, flags=re.S)
    for tok in PDF_ACTIVE:
        if re.search(re.escape(tok) + rb"(?![a-z])", low):
            raise ValidationError("PDF contains active content (JavaScript/launch/embedded files) and was rejected")
    if len(_PDF_PAGE.findall(data)) > MAX_PDF_PAGES:
        raise ValidationError(f"PDF has more than {MAX_PDF_PAGES} pages")
    # The raw scan above cannot see compressed object streams. Ask poppler: it parses the real document, so a
    # corrupt / truncated / password-protected file is rejected NOW, with a clear message, not deep inside processing.
    try:
        out = media._run(["pdfinfo", path], timeout=60).stdout.decode(errors="replace")
    except media.MediaTimeout:
        raise ValidationError("the PDF took too long to inspect; it may be damaged")
    except media.MediaError as e:
        msg = str(e)
        if "not found on PATH" in msg:
            raise
        if "ncorrect password" in msg or "ncrypted" in msg:
            raise ValidationError("password-protected PDFs are not supported; remove the password and upload again")
        raise ValidationError("the PDF is damaged or unreadable")
    m = re.search(r"^Pages:\s+(\d+)", out, re.M)
    if not m or int(m.group(1)) < 1:
        raise ValidationError("the PDF contains no pages")
    if int(m.group(1)) > MAX_PDF_PAGES:
        raise ValidationError(f"PDF has more than {MAX_PDF_PAGES} pages")


def _check_pptx_zip(z: zipfile.ZipFile) -> None:
    infos = z.infolist()
    if len(infos) > MAX_PPTX_ENTRIES:
        raise ValidationError("PPTX has too many entries")
    total = 0
    for i in infos:
        n = i.filename
        if n.startswith(("/", "\\")) or "\\" in n or ".." in n.split("/") or "\x00" in n or (len(n) > 1 and n[1] == ":"):
            raise ValidationError("PPTX contains an unsafe path")
        if i.flag_bits & 0x1:
            raise ValidationError("encrypted PPTX entries are not supported")
        if i.compress_size and i.file_size > 1_000_000 and i.file_size / i.compress_size > MAX_ZIP_RATIO:
            raise ValidationError("PPTX entry has a suspicious compression ratio")
        total += i.file_size
    if total > MAX_UNCOMPRESSED_BYTES:
        raise ValidationError("PPTX expands beyond the allowed size")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _mime_ok(declared: str, allowed_prefixes=(), allowed_exact=()) -> bool:
    d = (declared or "").split(";")[0].strip().lower()
    return d in GENERIC_MIME or d in allowed_exact or any(d.startswith(p) for p in allowed_prefixes)


def _canonical_mime(t: str, ext: str) -> str:
    return {"pdf": "application/pdf",
            "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}.get(
        t, "audio/mpeg" if ext == ".mp3" else ("video/mp4" if t == "video" else "audio/mp4"))


def validate_file(path: str, filename: str, declared_mime: str = "") -> ValidatedFile:
    ext = os.path.splitext(filename or "")[1].lower()
    size = os.path.getsize(path)
    if size == 0:
        raise ValidationError("file is empty")
    if size > MAX_BYTES:
        raise ValidationError(f"file exceeds the {MAX_BYTES // (1024 * 1024)} MB limit")
    with open(path, "rb") as f:
        head = f.read(4096)

    duration = None
    if ext in MEDIA_EXT:
        if not _mime_ok(declared_mime, (), MP3_MIME if ext == ".mp3" else MP4_MIME):
            raise ValidationError(f"MIME type {declared_mime!r} does not match a{'n MP3' if ext == '.mp3' else 'n MP4'} file")
        try:
            info = media.probe(path)
        except media.MediaTimeout:
            raise ValidationError("the file took too long to inspect; try a smaller or re-encoded file")
        except media.MediaError as e:
            if "not found on PATH" in str(e):
                raise                                       # a missing FFmpeg is a server problem, not a bad upload
            raise ValidationError("file is not valid audio/video (ffprobe could not parse it)")
        if not (info["has_audio"] or info["has_video"]):
            raise ValidationError("file has no audio or video stream")
        if not info["has_audio"]:
            raise ValidationError("the video has no audio track, so there is nothing to transcribe")
        if info["duration"] <= 0:
            raise ValidationError("could not determine media duration")
        if info["duration"] > MAX_DURATION_SEC:
            raise ValidationError(f"media longer than the {MAX_DURATION_SEC / 3600:g} h limit")
        fmts = {x for x in (info.get("format_name") or "").split(",") if x}
        if not (fmts & MEDIA_FORMATS[ext]) or fmts & {"hls", "concat", "sdp", "image2", "lavfi", "rtsp"}:
            raise ValidationError(f"the content is not a real {ext[1:].upper()} file (container: {info.get('format_name') or 'unknown'})")
        if ext == ".mp3" and info["has_video"]:
            raise ValidationError("the MP3 contains a video stream")
        t, duration = ("video" if info["has_video"] else "audio"), info["duration"]
    elif ext in EXT["pdf"]:
        if not _mime_ok(declared_mime, (), {"application/pdf"}):
            raise ValidationError(f"MIME type {declared_mime!r} does not match a PDF")
        if b"%PDF-" not in head[:1024]:
            raise ValidationError("file is not a PDF (bad signature)")
        _check_pdf(path, size)
        t = "pdf"
    elif ext in EXT["pptx"]:
        if not _mime_ok(declared_mime, (), PPTX_MIME):
            raise ValidationError(f"MIME type {declared_mime!r} does not match a PPTX")
        if not head.startswith(b"PK\x03\x04"):
            raise ValidationError("file is not a PPTX (bad signature)")
        try:
            with zipfile.ZipFile(path) as z:
                _check_pptx_zip(z)
                names = z.namelist()
                if "[Content_Types].xml" not in names or not any(n.startswith("ppt/") for n in names):
                    raise ValidationError("zip is not a PowerPoint document")
                if not any(re.fullmatch(r"ppt/slides/slide\d+\.xml", n) for n in names):
                    raise ValidationError("the presentation contains no slides")
        except zipfile.BadZipFile:
            raise ValidationError("file is not a valid PPTX archive")
        t = "pptx"
    else:
        raise ValidationError(f"unsupported file type {ext or '(none)'!r}; supported formats: {SUPPORTED_LABEL}")

    return ValidatedFile(t, ext, _canonical_mime(t, ext), size, sha256_file(path), duration)
