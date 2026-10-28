"""Shared test/dev helpers: an isolated environment (SQLite repo + local
storage) and one-call lecture creation through the real services."""
import os
from typing import Callable, List, Optional

from . import pipeline
from .assets import AssetService
from .repo import SqliteLectureRepository
from .service import LectureService
from .storage import LocalStorage


class Env:
    def __init__(self, root: str):
        self.repo = SqliteLectureRepository(os.path.join(root, "t.db"))
        self.storage = LocalStorage(os.path.join(root, "objects"), signing_secret="test-secret")
        self.lectures = LectureService(self.repo)
        self.assets = AssetService(self.repo, self.storage)

    def lecture(self, user: int = 1, media: Optional[str] = None, deck: Optional[str] = None, title="t",
                course_id=None, workspace_id=None, language=None, mime=("", "")) -> int:
        lid = self.lectures.create(user, title, course_id, workspace_id, language)
        if media:
            self.assets.add_from_file(user, lid, media, os.path.basename(media), mime[0])
        if deck:
            self.assets.add_from_file(user, lid, deck, os.path.basename(deck), mime[1])
        return lid

    def jobs(self, transcriber: Optional[Callable] = None, **kw):
        return pipeline.make_job_service(self.repo, self.storage, transcriber, backoff_base=0, **kw)

    def process(self, lid: int, transcriber: Optional[Callable] = None, max_attempts: int = 1):
        """Real pipeline run. Raises the stage's exception on failure."""
        return pipeline.run_pipeline(self.repo, self.storage, lid, transcriber)


def segments(*segs: List) -> Callable:
    """Test-input transcriber: returns the given Segment objects (stands in
    for Whisper, which is unavailable in the unit-test environment)."""
    return lambda wav, lang, prog, dur: list(segs)
