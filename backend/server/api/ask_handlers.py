"""Ask Notiv endpoint logic (framework-neutral; same contract as workspace_handlers).

All of these authorize through core.lecture.ask.AskService -> authz.get_lecture, so a lecture that is missing,
not yours, or not processed yields 404 / 404 / 409 exactly like the workspace endpoints. Conversations are
private to (lecture, user): no handler can read or clear another user's chat.
"""
from typing import List, Optional

from core.lecture import config, quotas
from core.lecture.ask import AskService
from server.api.workspace_handlers import ApiError, _call  # noqa: F401  (ApiError re-exported for routes/tests)


def _svc() -> AskService:
    return AskService(config.get_repo(), config.get_storage(), config.get_llm(), config.get_embedder())


def _with_ask_quota(fn, user_id, *a):
    getattr(config.get_repo(), "_ensure", lambda: None)()
    quotas.check_ask(user_id)
    return fn(user_id, *a)


def chat(user_id: int, lecture_id: int, question: str):
    return _call(_with_ask_quota, _svc().chat, user_id, lecture_id, question)


def chat_history(user_id: int, lecture_id: int):
    return _call(_svc().history, user_id, lecture_id)


def chat_reset(user_id: int, lecture_id: int):
    return _call(_svc().reset, user_id, lecture_id)


def retrieve(user_id: int, lecture_id: int, q: str = "", limit: int = 8):
    return _call(_svc().retrieve, user_id, lecture_id, q, limit)


def ask_stateless(user_id: int, lecture_id: int, question: str, history: Optional[List[dict]] = None):
    return _call(_with_ask_quota, _svc().ask_stateless, user_id, lecture_id, question, history)


def general_knowledge(user_id: int, lecture_id: int, message_id: int):
    """Explicit, labelled general-knowledge explanation for an answer that said 'not in your material'."""
    return _call(_with_ask_quota, _svc().general_knowledge, user_id, lecture_id, message_id)
