"""Builds a REAL (synthetic-content) lecture fixture with ffmpeg + Pillow:
a multi-page PDF deck and an H.264/AAC video that shows those slides at
known times, letterboxed and re-encoded lossy like a real recording.

This is test input only — it contains no speech, so it cannot exercise
transcription. Ground-truth slide change times are returned for assertions.
"""
import os
import subprocess

FFMPEG_TIMEOUT = 120      # bounded: a hung ffmpeg must fail a test, never hang collection/CI
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFont

TITLES = ["Gradient Descent", "Backpropagation", "Regularization", "Convolutions"]


def _font(size):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def render_slide(i: int, size=(1280, 720)) -> Image.Image:
    # Same template on every slide (header bar, white body) — the hard case
    # for matching — with different title, bullets and a different figure.
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, size[0], 110], fill=(20, 60, 120))
    d.text((50, 25), TITLES[i % len(TITLES)], fill="white", font=_font(56))
    for b in range(3):
        d.text((90, 170 + b * 80), f"- point {b + 1} about {TITLES[i % len(TITLES)].lower()}",
               fill="black", font=_font(36))
    cx, cy = 900, 450
    if i % 4 == 0:
        d.ellipse([cx - 150, cy - 150, cx + 150, cy + 150], outline="red", width=12)
    elif i % 4 == 1:
        d.rectangle([cx - 160, cy - 100, cx + 160, cy + 100], fill=(30, 140, 60))
    elif i % 4 == 2:
        d.polygon([(cx, cy - 160), (cx + 170, cy + 130), (cx - 170, cy + 130)], fill=(220, 150, 0))
    else:
        for k in range(6):
            d.line([(cx - 160, cy - 120 + k * 50), (cx + 160, cy - 100 + k * 40)], fill="purple", width=8)
    return img


def make_lecture(out_dir: str, durations: List[float] = (6, 9, 5, 7)) -> Tuple[str, str, List[float]]:
    """Returns (video_path, pdf_path, true_change_times)."""
    os.makedirs(out_dir, exist_ok=True)
    imgs = [render_slide(i) for i in range(len(durations))]
    pdf = os.path.join(out_dir, "deck.pdf")
    imgs[0].save(pdf, save_all=True, append_images=imgs[1:], resolution=100)

    seg_files = []
    for i, (im, dur) in enumerate(zip(imgs, durations)):
        png = os.path.join(out_dir, f"s{i}.png")
        im.save(png)
        seg = os.path.join(out_dir, f"seg{i}.mp4")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", "25", "-t", str(dur),
                        "-i", png, "-f", "lavfi", "-t", str(dur), "-i", "sine=frequency=220:sample_rate=44100",
                        "-vf", "scale=854:480:force_original_aspect_ratio=decrease,pad=960:480:(ow-iw)/2:(oh-ih)/2:black",
                        "-c:v", "libx264", "-crf", "30", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", seg],
                       check=True, timeout=FFMPEG_TIMEOUT, stdin=subprocess.DEVNULL)
        seg_files.append(seg)
    lst = os.path.join(out_dir, "list.txt")
    with open(lst, "w") as f:
        f.writelines(f"file '{s}'\n" for s in seg_files)
    video = os.path.join(out_dir, "lecture.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", video],
                   check=True, timeout=FFMPEG_TIMEOUT, stdin=subprocess.DEVNULL)
    changes, t = [], 0.0
    for d in durations[:-1]:
        t += d
        changes.append(t)
    return video, pdf, changes


def make_text_pdf(out_dir: str, n: int = 4, slide_lines=None, name: str = "deck_text.pdf") -> str:
    """Same slide images as make_lecture's deck, plus an invisible text layer
    (what a real exported PDF deck has), so slide text extraction and slide
    evidence are exercised. Requires reportlab (test input only)."""
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    os.makedirs(out_dir, exist_ok=True)
    pdf = os.path.join(out_dir, name)
    c = canvas.Canvas(pdf, pagesize=(960, 540))
    for i in range(n):
        c.drawImage(ImageReader(render_slide(i)), 0, 0, 960, 540)
        t = c.beginText(40, 480)
        t.setTextRenderMode(3)                        # invisible, selectable/extractable
        title = TITLES[i % len(TITLES)]
        lines = slide_lines[i] if slide_lines else [title] + [f"- point {b + 1} about {title.lower()}" for b in range(3)]
        for line in lines:
            t.textLine(line)
        c.drawText(t)
        c.showPage()
    c.save()
    return pdf
