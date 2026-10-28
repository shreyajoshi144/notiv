"""Lecture-level application service: creation + processing requests, with
authorization. Hierarchy: User -> Workspace -> Course -> Lecture -> Assets."""
from typing import Optional

from . import authz
from .errors import ValidationError
from .jobs import JobService
from .repo import LectureRepository


class LectureService:
    def __init__(self, repo: LectureRepository):
        self.repo = repo

    def create(self, user_id: int, title: str, course_id: Optional[int] = None,
               workspace_id: Optional[int] = None, language: Optional[str] = None) -> int:
        if course_id is not None:
            course = authz.get_course(self.repo, user_id, course_id)
            if workspace_id is not None and workspace_id != course["workspace_id"]:
                raise ValidationError("course belongs to a different workspace")
            workspace_id = course["workspace_id"]
        if workspace_id is None:
            workspace_id = self.repo.ensure_personal_workspace(user_id)
        else:
            authz.require_workspace(self.repo, user_id, workspace_id)
        return self.repo.create_lecture(user_id, title, language, course_id, workspace_id)

    def create_course(self, user_id: int, name: str, workspace_id: Optional[int] = None) -> int:
        if workspace_id is None:
            workspace_id = self.repo.ensure_personal_workspace(user_id)
        else:
            authz.require_workspace(self.repo, user_id, workspace_id)
        return self.repo.create_course(user_id, name, workspace_id)

    def request_processing(self, user_id: int, lecture_id: int, jobs: JobService, max_attempts: int = 3) -> int:
        authz.get_lecture(self.repo, user_id, lecture_id)
        return jobs.enqueue(lecture_id, max_attempts)

    def status(self, user_id: int, lecture_id: int) -> dict:
        lec = authz.get_lecture(self.repo, user_id, lecture_id)
        jobs = self.repo.list_jobs(lecture_id)
        return {**{k: lec[k] for k in ("id", "title", "status", "stage", "progress_pct", "error", "duration",
                                        "course_id", "workspace_id")},
                "job": jobs[-1] if jobs else None,
                "stages": self.repo.list_stages(jobs[-1]["id"]) if jobs else []}
