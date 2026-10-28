"""Run the real pipeline locally on a recording and/or deck and write a viewer.

    python -m core.lecture.cli lecture.mp4 slides.pdf [--language en] [--out viewer.html]
    (either file alone also works; audio files are fine)
"""
import argparse
import os
import shutil
import sys

from . import config, pipeline, viewer
from .assets import AssetService
from .service import LectureService


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", help="a recording (video/audio) and/or a .pdf/.pptx deck")
    ap.add_argument("--language", default=None)
    ap.add_argument("--out", default="viewer.html")
    ap.add_argument("--user", type=int, default=0)
    a = ap.parse_args(argv)

    repo, storage = config.get_repo(), config.get_storage()
    deck = next((f for f in a.files if f.lower().endswith((".pdf", ".pptx"))), "")
    rec = next((f for f in a.files if f != deck), "")
    lectures, assets = LectureService(repo), AssetService(repo, storage)
    lid = lectures.create(a.user, os.path.basename(rec or deck), language=a.language)
    for f in (rec, deck):
        if f:
            assets.add_from_file(a.user, lid, f, os.path.basename(f))
    pipeline.run_pipeline(repo, storage, lid)

    out, href = os.path.abspath(a.out), ""
    if rec:
        href = os.path.basename(rec)
        dest = os.path.join(os.path.dirname(out), href)
        if os.path.abspath(rec) != dest:
            shutil.copyfile(rec, dest)
    viewer.export_html(repo, storage, lid, out, href)
    d = repo.viewer_data(lid)
    print(f"lecture {lid}: {len(d['slides'])} slides, {len(d['timeline'])} timeline entries, "
          f"{len(d['segments'])} transcript segments -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
