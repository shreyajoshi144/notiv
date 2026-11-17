"""Notebook and item export as a readable PDF: the only download format.

Every item type is laid out the way it reads in the app (summary sections, concept definitions, flashcards as
question/answer pairs, a quiz with an answer key, the concept map drawn as a diagram) instead of raw JSON. Source
references are printed as the same short labels the app shows (page, slide or timestamp).
"""
from __future__ import annotations

import io
import json
import math
import os
import re
import time
from html import escape
from typing import Any, Iterable, List

from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (CondPageBreak, KeepTogether, ListFlowable, ListItem, PageBreak, Paragraph,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

INK, INK2, INK3 = colors.HexColor("#1c1d2e"), colors.HexColor("#4b4d66"), colors.HexColor("#73758e")
CLAY, GOLD, RULE = colors.HexColor("#4f62d1"), colors.HexColor("#9c7020"), colors.HexColor("#e2e0ea")
TINT, PAPER = colors.HexColor("#f1f0fb"), colors.HexColor("#faf8f3")
TYPE_LABEL = {"note": "Note", "summary": "Summary", "concepts": "Concepts", "definitions": "Definitions",
              "flashcards": "Flashcards", "quiz": "Quiz", "mind_map": "Concept map", "presentation": "Presentation",
              "study_guide": "Study guide", "brainstorm": "Brainstorm"}

# ── fonts: a Unicode TTF when the system has one (Docker image ships DejaVu / Liberation / Noto), else Helvetica ──
_FONT_CANDIDATES = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
     "/System/Library/Fonts/Supplemental/Arial Italic.ttf"),
]
_FONTS: tuple | None = None


def _fonts() -> tuple:
    global _FONTS
    if _FONTS:
        return _FONTS
    for reg, bold, ital in _FONT_CANDIDATES:
        if all(os.path.exists(p) for p in (reg, bold, ital)):
            try:
                pdfmetrics.registerFont(TTFont("NotivSans", reg)); pdfmetrics.registerFont(TTFont("NotivSans-Bold", bold))
                pdfmetrics.registerFont(TTFont("NotivSans-Italic", ital))
                from reportlab.lib.fonts import addMapping
                addMapping("NotivSans", 0, 0, "NotivSans"); addMapping("NotivSans", 1, 0, "NotivSans-Bold")
                addMapping("NotivSans", 0, 1, "NotivSans-Italic"); addMapping("NotivSans", 1, 1, "NotivSans-Bold")
                _FONTS = ("NotivSans", "NotivSans-Bold", "NotivSans-Italic", True)
                return _FONTS
            except Exception:
                continue
    _FONTS = ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique", False)
    return _FONTS


_ASCII = {"→": "->", "←": "<-", "↔": "<->", "≈": "~", "≤": "<=", "≥": ">=", "×": "x", "−": "-", "•": "-", "✓": "v"}


def _clean(text: Any) -> str:
    s = "" if text is None else str(text)
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s)
    if not _fonts()[3]:  # Helvetica covers Latin-1 and typographic quotes only
        s = "".join(_ASCII.get(ch, ch) for ch in s)
        s = s.encode("cp1252", "replace").decode("cp1252")
    return s


def _esc(text: Any) -> str:
    return escape(_clean(text))


def _inline(text: Any) -> str:
    """Escaped text with the app's tiny markdown: **bold**, *italic*, `code`."""
    s = _esc(text)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\w*])\*([^*\s][^*]*)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"`([^`]+)`", r'<font face="Courier">\1</font>', s)
    return s


def _styles() -> dict:
    reg, bold, ital, _ = _fonts()
    base = dict(fontName=reg, textColor=INK, alignment=TA_LEFT)
    return {
        "cover": ParagraphStyle("cover", fontName=bold, fontSize=26, leading=31, textColor=INK, spaceAfter=6),
        "coversub": ParagraphStyle("coversub", fontName=reg, fontSize=11, leading=15, textColor=INK3, spaceAfter=14),
        "kicker": ParagraphStyle("kicker", fontName=bold, fontSize=8, leading=10, textColor=CLAY, spaceAfter=3),
        "h1": ParagraphStyle("h1", fontName=bold, fontSize=17, leading=21, textColor=INK, spaceAfter=4),
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=12.5, leading=16, textColor=INK, spaceBefore=8, spaceAfter=3),
        "h3": ParagraphStyle("h3", fontName=bold, fontSize=11, leading=14, textColor=INK, spaceBefore=6, spaceAfter=2),
        "body": ParagraphStyle("body", fontSize=10.2, leading=14.6, spaceAfter=5, **base),
        "quote": ParagraphStyle("quote", fontName=ital, fontSize=10.2, leading=14.6, textColor=INK2, leftIndent=10,
                                borderPadding=(2, 0, 2, 8), borderColor=GOLD, borderWidth=0, spaceAfter=5),
        "muted": ParagraphStyle("muted", fontSize=8.8, leading=12, textColor=INK3, spaceAfter=4, **{k: v for k, v in base.items() if k != "textColor"}),
        "cite": ParagraphStyle("cite", fontName=reg, fontSize=8.3, leading=11, textColor=GOLD, spaceAfter=6),
        "cell": ParagraphStyle("cell", fontSize=9.6, leading=13, **base),
        "cellb": ParagraphStyle("cellb", fontName=bold, fontSize=9.6, leading=13, textColor=INK),
        "warn": ParagraphStyle("warn", fontSize=9.4, leading=13, textColor=colors.HexColor("#7a4b00"), backColor=colors.HexColor("#fdf3dc"),
                               borderPadding=6, spaceBefore=4, spaceAfter=10, **{k: v for k, v in base.items() if k != "textColor"}),
    }


# ── helpers ──────────────────────────────────────────────────────────────────────────────
def _cite_labels(x: Any) -> List[str]:
    out, seen = [], set()
    for e in (x.get("evidence") or []) if isinstance(x, dict) else []:
        if not isinstance(e, dict) or e.get("available") is False:
            continue
        c = e.get("citation") or {}
        label = c.get("label")
        if not label and c.get("seek_seconds") is not None:
            s = int(c["seek_seconds"]); label = f"{s // 60}:{s % 60:02d}"
        if label and label not in seen:
            seen.add(label); out.append(str(label))
    for key, fmt in (("pages", "p. {}"), ("times", "{}")):
        for v in (x.get(key) or []) if isinstance(x, dict) else []:
            label = fmt.format(v if key == "pages" else (f"{int(v) // 60}:{int(v) % 60:02d}" if isinstance(v, (int, float)) else v))
            if label not in seen:
                seen.add(label); out.append(label)
    return out[:8]


def _sources(x: Any, st: dict) -> list:
    labels = _cite_labels(x)
    return [Paragraph("Source: " + " · ".join(_esc(l) for l in labels), st["cite"])] if labels else []


def _bullets(items: Iterable, st: dict, numbered: bool = False) -> ListFlowable:
    return ListFlowable([ListItem(p if not isinstance(p, str) else Paragraph(p, st["body"]), leftIndent=12) for p in items],
                        bulletType="1" if numbered else "bullet", start=1 if numbered else None, leftIndent=14,
                        bulletFontSize=8 if not numbered else 9.5, bulletColor=INK3, bulletFontName=_fonts()[0])


def _markdown(src: str, st: dict) -> list:
    out, para, lst, kind = [], [], [], None

    def flush():
        nonlocal para
        if para:
            out.append(Paragraph(_inline(" ".join(para)), st["body"])); para = []

    def end_list():
        nonlocal lst, kind
        if lst:
            out.append(_bullets(lst, st, kind == "ol")); lst, kind = [], None

    for raw in str(src or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush(); end_list(); continue
        m = re.match(r"^(#{1,3})\s+(.*)$", line)
        if m:
            flush(); end_list(); out.append(Paragraph(_inline(m.group(2)), st["h2" if len(m.group(1)) == 1 else "h3"])); continue
        m = re.match(r"^\s*[-*]\s+(.*)$", line)
        if m:
            flush()
            if kind != "ul": end_list(); kind = "ul"
            lst.append(_inline(m.group(1))); continue
        m = re.match(r"^\s*\d+[.)]\s+(.*)$", line)
        if m:
            flush()
            if kind != "ol": end_list(); kind = "ol"
            lst.append(_inline(m.group(1))); continue
        end_list(); para.append(line.strip())
    flush(); end_list()
    return out or [Paragraph("This note is empty.", st["muted"])]


def _box(rows: list, widths: list, header: bool = False, zebra: bool = True) -> Table:
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
             ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
             ("LINEBELOW", (0, 0), (-1, -1), 0.5, RULE), ("BOX", (0, 0), (-1, -1), 0.6, RULE)]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), TINT)]
    if zebra:
        style += [("ROWBACKGROUNDS", (0, 1 if header else 0), (-1, -1), [colors.white, PAPER])]
    t.setStyle(TableStyle(style))
    return t


# ── per-type bodies ──────────────────────────────────────────────────────────────────────
def _summary(c: dict, st: dict) -> list:
    out = []
    if c.get("note"):
        out.append(Paragraph(_esc(c["note"]), st["muted"]))
    if c.get("overview"):
        out += [Paragraph(_esc(c.get("title") or "Overview"), st["h2"]), Paragraph(_esc(c["overview"]), st["body"])]
    if c.get("takeaways"):
        out.append(Paragraph("Key takeaways", st["h2"]))
        out.append(_bullets([[Paragraph(_esc(t.get("text") if isinstance(t, dict) else t), st["body"]), *_sources(t, st)]
                             if isinstance(t, dict) and _cite_labels(t) else _esc(t.get("text") if isinstance(t, dict) else t)
                             for t in c["takeaways"]], st))
    ai = c.get("mode") == "ai"
    for s in c.get("sections") or []:
        block = [Paragraph(_esc(s.get("title") or "Section"), st["h3"])]
        if s.get("body"):
            block.append(Paragraph(_esc(s["body"]), st["body" if ai else "quote"]))
        out.append(KeepTogether(block + _sources(s, st)))
    return out or [Paragraph("This summary has no sections.", st["muted"])]


def _knowledge(c: dict, st: dict) -> list:
    items = c.get("items") or c.get("concepts") or []
    if not items:
        return [Paragraph("Nothing was extracted. The lecture may not state definitions or key terms explicitly.", st["muted"])]
    out = []
    for k in items:
        ai = k.get("origin") == "ai_generated"
        block = [Paragraph(_esc(k.get("title") or "Term"), st["h3"])]
        if k.get("body"):
            block.append(Paragraph(_esc(k["body"]), st["body" if ai else "quote"]))
        elif ai and k.get("not_in_source_note"):
            block.append(Paragraph(_esc(k["not_in_source_note"]), st["muted"]))
        if ai and k.get("supplementary_explanation"):
            block += [Paragraph(_esc(k.get("supplementary_label") or "Supplementary explanation (AI): not from your notes"), st["muted"]),
                      Paragraph(_esc(k["supplementary_explanation"]), st["body"])]
        out.append(KeepTogether(block + _sources(k, st)))
    return out


def _flashcards(c: dict, st: dict, width: float) -> list:
    items = c.get("items") or []
    if not items:
        return [Paragraph("No flashcards could be built from this lecture's evidence yet.", st["muted"])]
    rows = [[Paragraph("#", st["cellb"]), Paragraph("Question", st["cellb"]), Paragraph("Answer", st["cellb"])]]
    for i, it in enumerate(items, 1):
        ans = [Paragraph(_esc(it.get("answer") or ""), st["cell"])]
        if it.get("explanation"):
            ans.append(Paragraph(_esc(it["explanation"]), st["muted"]))
        ans += _sources(it, st)
        rows.append([Paragraph(str(i), st["cell"]), Paragraph(_esc(it.get("prompt") or ""), st["cell"]), ans])
    return [Paragraph(f"{len(items)} card{'s' if len(items) != 1 else ''}. Cover the answer column to test yourself.", st["muted"]),
            _box(rows, [9 * mm, (width - 9 * mm) * 0.46, (width - 9 * mm) * 0.54], header=True)]


def _quiz(c: dict, st: dict) -> list:
    items = c.get("items") or []
    if not items:
        return [Paragraph("No quiz questions could be built from this lecture's evidence yet.", st["muted"])]
    out = [Paragraph(f"{len(items)} question{'s' if len(items) != 1 else ''}. The answer key follows the questions.", st["muted"])]
    for n, it in enumerate(items, 1):
        opts = [f"<b>{_esc(str(o.get('id', '')).upper())}.</b>  {_esc(o.get('text'))}" for o in it.get("options") or []]
        out.append(KeepTogether([Paragraph(f"{n}. {_esc(it.get('prompt'))}", st["h3"]),
                                 *[Paragraph(o, ParagraphStyle("opt", parent=st["body"], leftIndent=14, spaceAfter=2)) for o in opts],
                                 Spacer(1, 4)]))
    out += [CondPageBreak(60 * mm), Paragraph("Answer key", st["h2"])]
    for n, it in enumerate(items, 1):
        correct = str(it.get("correct_option") or "").upper()
        text = next((o.get("text") for o in it.get("options") or [] if str(o.get("id", "")).upper() == correct), "")
        line = f"<b>{n}. {_esc(correct)}</b>" + (f": {_esc(text)}" if text else "")
        if it.get("explanation"):
            line += f"<br/><font color='#4b4d66'>{_esc(it['explanation'])}</font>"
        out += [Paragraph(line, st["body"]), *_sources(it, st)]
    return out


def _map_drawing(nodes: list, edges: list, width: float, title: str = "") -> Drawing | None:
    nodes = [n for n in nodes if isinstance(n, dict)]
    if not nodes:
        return None
    W, H = 200.0, 46.0
    has_xy = sum(1 for n in nodes if n.get("x") is not None and n.get("y") is not None) == len(nodes)
    if has_xy:
        pos = {str(n.get("id")): (float(n.get("x") or 0), float(n.get("y") or 0)) for n in nodes}
    else:  # lay out on a grid when positions are missing
        cols = max(1, math.ceil(math.sqrt(len(nodes))))
        pos = {str(n.get("id")): ((i % cols) * (W + 40), (i // cols) * (H + 50)) for i, n in enumerate(nodes)}
    xs, ys = [p[0] for p in pos.values()], [p[1] for p in pos.values()]
    minx, miny = min(xs), min(ys)
    vw, vh = max(xs) - minx + W + 20, max(ys) - miny + H + 20
    scale = min(1.0, width / vw, (230 * mm) / vh)
    d = Drawing(vw * scale, vh * scale)
    font = _fonts()[0]
    P = {k: ((x - minx + 10) * scale, (vh - (y - miny + 10) - H) * scale) for k, (x, y) in pos.items()}
    w, hgt = W * scale, H * scale
    for e in edges or []:
        a, b = P.get(str(e.get("from"))), P.get(str(e.get("to")))
        if not (a and b):
            continue
        d.add(Line(a[0] + w / 2, a[1] + hgt / 2, b[0] + w / 2, b[1] + hgt / 2, strokeColor=GOLD, strokeWidth=1))
        if e.get("label"):
            d.add(String((a[0] + b[0]) / 2 + w / 2, (a[1] + b[1]) / 2 + hgt / 2 + 3, _clean(e["label"])[:28],
                         fontName=font, fontSize=max(5, 8 * scale), fillColor=INK2, textAnchor="middle"))
    fs = max(5.5, 10 * scale)
    max_chars = max(8, int(w / (fs * 0.52)))
    for n in nodes:
        x, y = P[str(n.get("id"))]
        d.add(Rect(x, y, w, hgt, rx=5 * scale, ry=5 * scale, fillColor=colors.white, strokeColor=CLAY, strokeWidth=0.8))
        words, lines, cur = _clean(n.get("text") or "").split(), [], ""
        for wd in words:
            if len(cur) + len(wd) + 1 > max_chars and cur:
                lines.append(cur); cur = wd
            else:
                cur = (cur + " " + wd).strip()
        if cur: lines.append(cur)
        lines = lines[:2] if len(lines) <= 2 else [lines[0], lines[1][: max_chars - 1] + "…"]
        for i, ln in enumerate(lines):
            d.add(String(x + w / 2, y + hgt / 2 + (len(lines) - 1) * fs * 0.55 - i * fs * 1.1 - fs * 0.35, ln,
                         fontName=font, fontSize=fs, fillColor=INK, textAnchor="middle"))
    return d


def _mind_map(c: dict, st: dict, width: float) -> list:
    nodes, edges = c.get("nodes") or [], c.get("edges") or []
    if not nodes:
        return [Paragraph(_esc(c.get("message") or "This map has no nodes."), st["muted"])]
    out = [d] if (d := _map_drawing(nodes, edges, width)) else []
    text = {str(n.get("id")): n.get("text") for n in nodes}
    rel = [f"{_esc(text.get(str(e.get('from'))))} <font color='#9c7020'>— {_esc(e.get('label') or 'relates to')} →</font> {_esc(text.get(str(e.get('to'))))}"
           for e in edges if str(e.get("from")) in text and str(e.get("to")) in text]
    if rel:
        out += [Paragraph("Connections", st["h3"]), _bullets(rel, st)]
    return out


def _fallback(c: Any, st: dict) -> list:
    if isinstance(c, str):
        return _markdown(c, st)
    if isinstance(c, dict) and isinstance(c.get("items"), list):
        return [_bullets([_esc(i.get("title") or i.get("prompt") or i.get("text") or json.dumps(i, ensure_ascii=False)) if isinstance(i, dict) else _esc(i)
                          for i in c["items"]], st)]
    return [Paragraph("This item has no printable content.", st["muted"])]


def item_flowables(item: dict, st: dict, width: float, lecture_names: dict | None = None) -> list:
    t, c = item.get("item_type"), item.get("content")
    out = [Paragraph(_esc(TYPE_LABEL.get(t, (t or "item").replace("_", " ").title())).upper(), st["kicker"]),
           Paragraph(_esc(item.get("title") or "Untitled"), st["h1"])]
    meta = []
    if item.get("updated_at"):
        try: meta.append("Saved " + time.strftime("%d %b %Y", time.localtime(float(item["updated_at"]))))
        except (TypeError, ValueError): pass
    built = sorted({(lecture_names or {}).get(int(p["source_id"]), f"Lecture {p['source_id']}")
                    for p in item.get("provenance") or [] if isinstance(p, dict) and p.get("source_type") == "lecture" and p.get("source_id")})
    if built:
        meta.append("Built from " + ", ".join(built))
    if meta:
        out.append(Paragraph(_esc(" · ".join(meta)), st["muted"]))
    out.append(Spacer(1, 4))
    if isinstance(c, dict) and c.get("ai_unavailable"):
        out.append(Paragraph("<b>AI study material unavailable.</b> " + _esc(c["ai_unavailable"]), st["warn"]))
    if isinstance(c, dict) and c.get("insufficient_evidence"):
        return out + [Paragraph("<b>Not enough evidence yet.</b> " + _esc(c.get("message") or ""), st["warn"])]
    try:
        if t in ("note", "study_guide", "presentation", "brainstorm"):
            body = _markdown(c, st) if isinstance(c, str) else _fallback(c, st)
        elif t == "summary" and isinstance(c, dict):
            body = _summary(c, st)
        elif t in ("concepts", "definitions") and isinstance(c, dict):
            body = _knowledge(c, st)
        elif t == "flashcards" and isinstance(c, dict):
            body = _flashcards(c, st, width)
        elif t == "quiz" and isinstance(c, dict):
            body = _quiz(c, st)
        elif t == "mind_map" and isinstance(c, dict):
            body = _mind_map(c, st, width)
        else:
            body = _fallback(c, st)
    except Exception:  # malformed legacy content: print what can be printed rather than failing the whole export
        body = _fallback(c, st)
    return out + body


# ── documents ────────────────────────────────────────────────────────────────────────────
def _doc(buf: io.BytesIO, title: str) -> SimpleDocTemplate:
    reg = _fonts()[0]
    stamp = time.strftime("%d %b %Y")

    def chrome(canvas, doc):
        canvas.saveState()
        canvas.setFont(reg, 7.8); canvas.setFillColor(INK3)
        canvas.drawString(doc.leftMargin, 12 * mm, _clean(f"Notiv · {title}")[:90])
        canvas.drawRightString(A4[0] - doc.rightMargin, 12 * mm, f"{stamp} · page {doc.page}")
        canvas.setStrokeColor(RULE); canvas.setLineWidth(0.5)
        canvas.line(doc.leftMargin, 15 * mm, A4[0] - doc.rightMargin, 15 * mm)
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=20 * mm, bottomMargin=22 * mm,
                            title=_clean(title), author="Notiv", subject="Notiv notebook export", creator="Notiv")
    doc._chrome = chrome
    return doc


def _build(doc: SimpleDocTemplate, story: list) -> None:
    doc.build(story, onFirstPage=doc._chrome, onLaterPages=doc._chrome)


def notebook_pdf(nb: dict) -> bytes:
    st, buf = _styles(), io.BytesIO()
    doc = _doc(buf, nb.get("title") or "Notebook")
    width = doc.width
    names = {int(s["source_id"]): s.get("label") or f"Lecture {s['source_id']}"
             for s in nb.get("sources") or [] if s.get("source_type") == "lecture" and s.get("source_id")}
    items = nb.get("items") or []
    story = [Paragraph("NOTIV NOTEBOOK", st["kicker"]), Paragraph(_esc(nb.get("title") or "Notebook"), st["cover"])]
    sub = [x for x in (nb.get("subject"), f"{len(items)} item{'s' if len(items) != 1 else ''}", "Exported " + time.strftime("%d %B %Y")) if x]
    story.append(Paragraph(_esc(" · ".join(sub)), st["coversub"]))
    if nb.get("description"):
        story.append(Paragraph(_esc(nb["description"]), st["body"]))
    if nb.get("sources"):
        story += [Paragraph("Sources", st["h2"]), _bullets([_esc(s.get("label") or f"{s.get('source_type')} {s.get('source_id')}") for s in nb["sources"]], st)]
    if items:
        story += [Paragraph("Contents", st["h2"]),
                  _bullets([f"{_esc(i.get('title') or 'Untitled')} <font color='#73758e'>({_esc(TYPE_LABEL.get(i.get('item_type'), i.get('item_type')))})</font>" for i in items], st, numbered=True)]
    else:
        story.append(Paragraph("This notebook has no notes or study material yet.", st["muted"]))
    for it in items:
        story += [PageBreak(), *item_flowables(it, st, width, names)]
    cv = nb.get("canvas") or {}
    if cv.get("nodes"):
        drawing = _map_drawing(cv["nodes"], cv.get("edges") or [], width)
        if drawing:
            story += [PageBreak(), Paragraph("CANVAS", st["kicker"]), Paragraph("Canvas", st["h1"]), Spacer(1, 6), drawing]
    _build(doc, story)
    return buf.getvalue()


def item_pdf(item: dict, nb: dict | None = None) -> bytes:
    st, buf = _styles(), io.BytesIO()
    doc = _doc(buf, item.get("title") or "Notebook item")
    names = {int(s["source_id"]): s.get("label") or f"Lecture {s['source_id']}"
             for s in (nb or {}).get("sources") or [] if s.get("source_type") == "lecture" and s.get("source_id")}
    story = item_flowables(item, st, doc.width, names)
    if nb and nb.get("title"):
        story.insert(0, Paragraph(_esc("From the notebook " + nb["title"]), st["muted"]))
    _build(doc, story)
    return buf.getvalue()
