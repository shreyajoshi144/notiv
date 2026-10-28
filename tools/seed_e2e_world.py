"""Seeds a Notiv database/storage (the same ones the server is started with) with PROCESSED lectures for browser E2E
and for local UI review. Every lecture runs through the REAL pipeline (ffmpeg-encoded video, PDF deck with a text layer,
slide-change detection, alignment, evidence, insights). The transcript lines are test INPUT (no speech model offline) and
come from core/study_fixtures.py — the same input the Sprint 4 tests use. Nothing here is product data and the UI never
fabricates anything: this only fills the database the product reads from.

    NOTIV_LECTURE_DB=... NOTIV_STORAGE_DIR=... NOTIV_SIGNING_SECRET=... \
    PYTHONPATH=backend:.:core python tools/seed_e2e_world.py --user 888 [--json out.json]

Prints (and optionally writes) {"user":888,"courses":{...},"lectures":{...}} with real ids.
"""
import argparse
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "core"), os.path.join(ROOT, "backend")):
    if p not in sys.path:
        sys.path.insert(0, p)

from core import study_fixtures as F                                           # noqa: E402
from core.lecture import config, pipeline                                     # noqa: E402
from core.lecture.align import Segment                                        # noqa: E402
from core.lecture.assets import AssetService                                  # noqa: E402
from core.lecture.make_test_lecture import make_lecture, make_text_pdf        # noqa: E402
from core.lecture.service import LectureService                               # noqa: E402
from core.lecture.testkit import segments                                     # noqa: E402


def seed(user: int) -> dict:
    repo, storage = config.get_repo(), config.get_storage()
    lectures, assets = LectureService(repo), AssetService(repo, storage)
    tmp = tempfile.mkdtemp(prefix="notiv-seed-")
    video, _, _ = make_lecture(os.path.join(tmp, "fx"), [6, 9, 5, 7])

    def make(title, segs, slides, course):
        deck = make_text_pdf(os.path.join(tmp, "fx"), slide_lines=slides, name=title.replace(" ", "_") + ".pdf")
        lid = lectures.create(user, title, course)
        assets.add_from_file(user, lid, video, os.path.basename(video), "")
        assets.add_from_file(user, lid, deck, os.path.basename(deck), "")
        pipeline.run_pipeline(repo, storage, lid, segments(*segs))
        return lid

    c_dl = lectures.create_course(user, "Deep Learning")
    c_db = lectures.create_course(user, "Databases")
    out = {"user": user, "courses": {"deep_learning": c_dl, "databases": c_db}, "lectures": {}}
    out["lectures"]["gradient_descent"] = make("Gradient descent and regularization", F.DL1, F.DL1_SLIDES, c_dl)
    out["lectures"]["optimizers"] = make("Learning rates and optimizers", F.DL2, F.DL2_SLIDES, c_dl)
    out["lectures"]["keys_and_joins"] = make("Keys, joins and normalization", F.DB, F.DB_SLIDES, c_db)
    out["lectures"]["empty"] = lectures.create(user, "Awaiting a recording", c_db)        # created, nothing uploaded yet
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", type=int, required=True)
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    result = seed(a.user)
    text = json.dumps(result)
    if a.json:
        with open(a.json, "w") as fh:
            fh.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
