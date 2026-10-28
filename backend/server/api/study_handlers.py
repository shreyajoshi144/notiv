"""Study intelligence endpoint logic (framework-neutral; same error contract as workspace_handlers).

Everything authorizes inside StudyService / CourseMemory (lecture workspace membership; course membership;
session ownership). Knowledge items, study items and sessions are addressed by their own ids, but access is always
decided through the owning lecture/course, so a foreign id is indistinguishable from a missing one (404).
"""
from typing import List, Optional

from core.lecture import config
from core.lecture.course_memory import CourseMemory
from core.lecture.study import StudyService
from server.api.workspace_handlers import ApiError, _call  # noqa: F401  (re-exported for routes/tests)


def _study() -> StudyService:
    return StudyService(config.get_repo(), config.get_storage())


def _course() -> CourseMemory:
    return CourseMemory(config.get_repo(), config.get_storage())


# knowledge ------------------------------------------------------------------
def knowledge(user_id: int, lecture_id: int, kind: Optional[str] = None, slide: Optional[int] = None, q: Optional[str] = None):
    return _call(_study().knowledge, user_id, lecture_id, kind, slide, q)


def concepts(user_id: int, lecture_id: int):
    return _call(_study().concepts, user_id, lecture_id)


def knowledge_regenerate(user_id: int, lecture_id: int):
    return _call(_study().regenerate_knowledge, user_id, lecture_id)


def knowledge_delete(user_id: int, knowledge_id: int):
    return _call(_study().delete_knowledge, user_id, knowledge_id)


def knowledge_review(user_id: int, knowledge_id: int):
    return _call(_study().review_concept, user_id, knowledge_id)


# study items -----------------------------------------------------------------
def study_items(user_id: int, lecture_id: int, kind: Optional[str] = None, difficulty: Optional[str] = None, reveal: bool = False):
    return _call(_study().list_items, user_id, lecture_id, kind, difficulty, reveal)


def study_generate(user_id: int, lecture_id: int, kinds: Optional[List[str]] = None):
    return _call(_study().generate_items, user_id, lecture_id, kinds)


def item_get(user_id: int, item_id: int):
    return _call(_study().get_item, user_id, item_id)


def item_edit(user_id: int, item_id: int, patch: dict):
    return _call(_study().edit_item, user_id, item_id, patch)


def item_delete(user_id: int, item_id: int):
    return _call(_study().delete_item, user_id, item_id)


def item_regenerate(user_id: int, item_id: int):
    return _call(_study().regenerate_item, user_id, item_id)


# sessions + progress ----------------------------------------------------------
def session_start_lecture(user_id: int, lecture_id: int, kinds: Optional[List[str]] = None, difficulty: Optional[str] = None, limit: int = 10,
                          include_mastered: bool = False):
    return _call(_study().start_session, user_id, "lecture", lecture_id, kinds, difficulty, limit, include_mastered)


def session_start_course(user_id: int, course_id: int, kinds: Optional[List[str]] = None, difficulty: Optional[str] = None, limit: int = 10,
                         include_mastered: bool = False):
    return _call(_study().start_session, user_id, "course", course_id, kinds, difficulty, limit, include_mastered)


def session_get(user_id: int, session_id: int):
    return _call(_study().session, user_id, session_id)


def session_attempt(user_id: int, session_id: int, item_id: int, selected_option: Optional[str] = None,
                    self_grade: Optional[str] = None, response_text: Optional[str] = None):
    return _call(_study().attempt, user_id, session_id, item_id, selected_option, self_grade, response_text)


def session_finish(user_id: int, session_id: int):
    return _call(_study().finish, user_id, session_id)


def progress_lecture(user_id: int, lecture_id: int):
    return _call(_study().progress, user_id, "lecture", lecture_id)


def progress_course(user_id: int, course_id: int):
    return _call(_study().progress, user_id, "course", course_id)


# course memory ----------------------------------------------------------------
def course_overview(user_id: int, course_id: int):
    return _call(_course().overview, user_id, course_id)


def course_knowledge(user_id: int, course_id: int, kind: Optional[str] = None, lecture: Optional[int] = None, q: Optional[str] = None):
    return _call(_course().knowledge, user_id, course_id, kind, lecture, q)


def course_concepts(user_id: int, course_id: int, shared_only: bool = False):
    return _call(_course().concepts, user_id, course_id, shared_only)


def course_search(user_id: int, course_id: int, q: str = "", limit: int = 20):
    return _call(_course().search, user_id, course_id, q, limit)


def course_study_items(user_id: int, course_id: int, kind: Optional[str] = None, difficulty: Optional[str] = None, reveal: bool = False):
    return _call(_course().study_items, user_id, course_id, kind, difficulty, reveal)
