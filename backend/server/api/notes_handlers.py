"""Personal-notes endpoint logic (framework-neutral; same error contract as workspace_handlers).

  GET    /lectures/{id}/notes?after_id=&limit=&evidence_id=   list MY notes for a lecture (paged by id)
  POST   /lectures/{id}/notes   {body, evidence_ids?, slide_index?, t?}   create
  GET    /notes/{id}            read one            PATCH /notes/{id}  {body?, evidence_ids?, slide_index?, t?}
  DELETE /notes/{id}            delete
404 = missing OR not yours OR you lost access to the lecture (indistinguishable); 400 = bad input; 429 = note quota.
"""
from typing import List, Optional

from core.lecture import config
from core.lecture.notes import NotesService
from server.api.workspace_handlers import ApiError, _call  # noqa: F401  (re-exported for routes/tests)


def _svc() -> NotesService:
    return NotesService(config.get_repo())


def notes_list(user_id: int, lecture_id: int, after_id: int = 0, limit: int = 100, evidence_id: Optional[int] = None):
    return _call(_svc().list, user_id, lecture_id, after_id, limit, evidence_id)


def note_create(user_id: int, lecture_id: int, body: str, evidence_ids: Optional[List[int]] = None,
                slide_index: Optional[int] = None, t: Optional[float] = None):
    return _call(_svc().create, user_id, lecture_id, body, evidence_ids, slide_index, t)


def note_get(user_id: int, note_id: int):
    return _call(_svc().get, user_id, note_id)


def note_update(user_id: int, note_id: int, patch: dict):
    return _call(lambda: _svc().update(user_id, note_id, **patch))


def note_delete(user_id: int, note_id: int):
    _call(_svc().delete, user_id, note_id)
    return {"deleted": True}
