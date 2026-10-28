"""Shared, process-wide test world for Sprint 4 (built once, torn down at exit). Every lecture goes through the
REAL pipeline (encoded video + PDF deck with a text layer); transcript lines are test input (see study_fixtures).

 users   ALICE=1, BOB=2, EVE=3 (EVE owns nothing)
 ALICE   course `dl`      "Deep Learning"   : DL1, DL2 (processed), plus a QUEUED lecture (must be ignored)
         course `db`      "Databases"       : DB1                              (different course, same owner)
         course `scratch` "Scratch"         : M (mutation tests), P (progress tests), EMPTY (no extractable
                                              knowledge), SPARSE (exactly two definitions)
 BOB     course `bob`     "Bob's course"    : BOBDL (same text as DL1, different owner)
 SHARED  workspace "Study group" (ALICE owner, BOB member): course `group`: GROUP (same text as DL1)
"""
import atexit
import os
import shutil
import tempfile
from types import SimpleNamespace

from core import study_fixtures as F
from core.lecture import config
from core.lecture.align import Segment
from core.lecture.make_test_lecture import make_lecture, make_text_pdf
from core.lecture.testkit import Env, segments

ALICE, BOB, EVE = 1, 2, 3
_world = None


def get_world() -> SimpleNamespace:
    global _world
    if _world is not None:
        return _world
    tmp = tempfile.mkdtemp(prefix="notiv-study-")
    atexit.register(shutil.rmtree, tmp, True)
    video, _, _ = make_lecture(os.path.join(tmp, "fx"), [6, 9, 5, 7])
    env = Env(tmp)
    config.configure(env.repo, env.storage)
    w = SimpleNamespace(tmp=tmp, env=env, repo=env.repo, storage=env.storage, video=video, ALICE=ALICE, BOB=BOB, EVE=EVE)

    def deck(name, slides):
        return make_text_pdf(os.path.join(tmp, "fx"), slide_lines=slides, name=name + ".pdf")

    def lecture(user, title, segs, slides=None, course=None, ws=None):
        lid = env.lecture(user, video, deck(title.replace(" ", "_"), slides) if slides else None, title=title, course_id=course, workspace_id=ws)
        env.process(lid, segments(*segs))
        return lid

    w.new_lecture = lecture                                  # tests that MUTATE lecture state build their own
    w.c_dl = env.lectures.create_course(ALICE, "Deep Learning")
    w.c_db = env.lectures.create_course(ALICE, "Databases")
    w.c_scratch = env.lectures.create_course(ALICE, "Scratch")
    w.DL1 = lecture(ALICE, "DL1", F.DL1, F.DL1_SLIDES, w.c_dl)
    w.DL2 = lecture(ALICE, "DL2", F.DL2, F.DL2_SLIDES, w.c_dl)
    w.queued = env.lectures.create(ALICE, "Not processed yet", w.c_dl)
    env.jobs().enqueue(w.queued)
    w.DB1 = lecture(ALICE, "DB1", F.DB, F.DB_SLIDES, w.c_db)
    w.M = lecture(ALICE, "Mutations", F.DL1, F.DL1_SLIDES, w.c_scratch)
    w.P = lecture(ALICE, "Progress", F.DL1, F.DL1_SLIDES, w.c_scratch)
    w.EMPTY = lecture(ALICE, "Small talk", [Segment(0.5, 3.0, "Okay so let us get started."), Segment(3.0, 5.5, "Thanks everyone and see you next week.")],
                      None, w.c_scratch)
    w.SPARSE = lecture(ALICE, "Two definitions", [Segment(0.5, 3.0, "A vector is defined as an ordered list of numbers."),
                                                  Segment(3.0, 6.0, "A matrix is defined as a rectangular array of numbers.")], None, w.c_scratch)
    w.c_bob = env.lectures.create_course(BOB, "Bob's course")
    w.BOBDL = lecture(BOB, "Bob DL", F.DL1, F.DL1_SLIDES, w.c_bob)
    w.ws_group = env.repo.create_workspace(ALICE, "Study group")
    env.repo.add_member(w.ws_group, BOB)
    w.c_group = env.lectures.create_course(ALICE, "Group course", w.ws_group)
    w.GROUP = lecture(ALICE, "Group DL", F.DL1, F.DL1_SLIDES, w.c_group)
    _world = w
    return w


class _LazyWorld:
    """`W = LAZY_WORLD` at module level costs nothing: the fixture world (ffmpeg encodes) is built on FIRST ATTRIBUTE
    ACCESS, i.e. inside setUpClass/tests, never at import/collection time."""
    def __getattr__(self, name):
        return getattr(get_world(), name)


LAZY_WORLD = _LazyWorld()
