"""Reusable authorization. Every lookup that starts from a client-supplied
id goes through here: resource -> its workspace -> caller must be a member.
A missing resource and a resource the caller can't see both raise
NotFoundError (indistinguishable, so ids can't be probed)."""
from .errors import NotFoundError
from .repo import LectureRepository


MAX_ID = 2 ** 63 - 1


def valid_id(i) -> bool:
    """Client-supplied ids must be positive 64-bit ints; anything else can never exist (-> plain NotFound, never a DB error)."""
    return isinstance(i, int) and not isinstance(i, bool) and 1 <= i <= MAX_ID


def _id(i) -> int:
    if not valid_id(i):
        raise NotFoundError("not found")
    return i


def require_workspace(repo: LectureRepository, user_id: int, workspace_id: int) -> int:
    if not valid_id(workspace_id) or not valid_id(user_id) or not repo.is_member(workspace_id, user_id):
        raise NotFoundError("not found")
    return workspace_id


def get_lecture(repo: LectureRepository, user_id: int, lecture_id: int) -> dict:
    lec = repo.get_lecture(_id(lecture_id))
    if not lec:
        raise NotFoundError("not found")
    require_workspace(repo, user_id, lec["workspace_id"])
    return lec


def get_course(repo: LectureRepository, user_id: int, course_id: int) -> dict:
    c = repo.get_course(_id(course_id))
    if not c:
        raise NotFoundError("not found")
    require_workspace(repo, user_id, c["workspace_id"])
    return c


def get_asset(repo: LectureRepository, user_id: int, asset_id: int) -> dict:
    a = repo.get_asset(_id(asset_id))
    if not a:
        raise NotFoundError("not found")
    require_workspace(repo, user_id, a["workspace_id"])
    return a


def get_job(repo: LectureRepository, user_id: int, job_id: int) -> dict:
    j = repo.get_job(_id(job_id))
    if not j:
        raise NotFoundError("not found")
    require_workspace(repo, user_id, j["workspace_id"])
    return j


def get_evidence(repo: LectureRepository, user_id: int, evidence_id: int) -> dict:
    ev = repo.get_evidence(_id(evidence_id))
    if not ev:
        raise NotFoundError("not found")
    get_lecture(repo, user_id, ev["lecture_id"])     # inherits the lecture's access rule
    return ev


def accessible_lecture_ids(repo: LectureRepository, user_id: int, lecture_ids) -> list:
    """Filters an id list down to lectures the user may see (used for any
    multi-lecture scope, e.g. a course)."""
    out = []
    for i in lecture_ids:
        if not valid_id(i):
            continue
        lec = repo.get_lecture(i)
        if lec and lec["workspace_id"] is not None and repo.is_member(lec["workspace_id"], user_id):
            out.append(i)
    return out
