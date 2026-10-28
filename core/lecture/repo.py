"""Repository boundary: services depend on LectureRepository, never on a
concrete database. SqliteLectureRepository (dev/tests) delegates to
core.lecture.store. A PostgreSQL(+pgvector) implementation targets this same
interface — see AUDIT.md for what remains to build it. (Generated from the
SQLite store's signatures so the two cannot drift; edit store.py, then
add the matching method here and to any other implementation.)
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Sequence

from . import store, study_store


class LectureRepository(ABC):
    @abstractmethod
    def create_workspace(self, owner_id: int, name: str) -> int: ...

    @abstractmethod
    def ensure_personal_workspace(self, user_id: int) -> int: ...

    @abstractmethod
    def add_member(self, workspace_id: int, user_id: int, role: str = 'member') -> None: ...

    @abstractmethod
    def is_member(self, workspace_id: int, user_id: int) -> bool: ...

    @abstractmethod
    def create_course(self, owner_id: int, name: str, workspace_id: Optional[int] = None) -> int: ...

    @abstractmethod
    def get_course(self, course_id: int) -> Optional[dict]: ...

    @abstractmethod
    def list_courses_for_user(self, user_id: int, limit: int = 100, offset: int = 0) -> List[dict]: ...

    @abstractmethod
    def list_lectures_for_user(self, user_id: int, limit: int = 100, offset: int = 0, course_id: Optional[int] = None) -> List[dict]: ...

    @abstractmethod
    def course_lecture_ids(self, course_id: int, only_done: bool = True) -> List[int]: ...

    @abstractmethod
    def create_lecture(self, owner_id: int, title: str, language: Optional[str] = None, course_id: Optional[int] = None, workspace_id: Optional[int] = None) -> int: ...

    @abstractmethod
    def get_lecture(self, lecture_id: int) -> Optional[dict]: ...

    @abstractmethod
    def set_status(self, lecture_id: int, status: str, stage: str = None, progress_pct: int = None, error: str = None, duration: float = None): ...

    @abstractmethod
    def delete_lecture_data(self, lecture_id: int) -> None: ...

    @abstractmethod
    def add_asset(self, lecture_id: int, workspace_id: int, **f) -> int: ...

    @abstractmethod
    def get_asset(self, asset_id: int) -> Optional[dict]: ...

    @abstractmethod
    def list_assets(self, lecture_id: int, type: Optional[str] = None, status: Optional[str] = None) -> List[dict]: ...

    @abstractmethod
    def update_asset(self, asset_id: int, **f) -> None: ...

    @abstractmethod
    def create_job(self, lecture_id: int, workspace_id: int, type: str, max_attempts: int = 3) -> int: ...

    @abstractmethod
    def get_job(self, job_id: int) -> Optional[dict]: ...

    @abstractmethod
    def list_jobs(self, lecture_id: int) -> List[dict]: ...

    @abstractmethod
    def update_job(self, job_id: int, **f) -> None: ...

    @abstractmethod
    def claim_next_job(self, now: float, job_id: Optional[int] = None, worker_id: Optional[str] = None) -> Optional[dict]: ...

    # Sprint 5 hooks (concrete defaults so older repository implementations keep working)
    def create_job_once(self, lecture_id: int, workspace_id: int, type: str, max_attempts: int = 3) -> tuple:
        """Idempotent enqueue -> (job_id, created)."""
        raise NotImplementedError

    def active_job(self, lecture_id: int, type: str) -> Optional[dict]:
        raise NotImplementedError

    def job_owned_by(self, job_id: int, worker_id: Optional[str] = None) -> bool:
        raise NotImplementedError

    def beat_worker(self, worker_id: Optional[str] = None, info: Optional[dict] = None) -> None:
        raise NotImplementedError

    def finalize_owned(self, job_id: int, worker_id: Optional[str], job: Optional[dict] = None,
                       lecture: Optional[dict] = None, stage: Optional[tuple] = None) -> bool:
        """Fenced, atomic write of a job outcome; False (nothing written) if this worker no longer holds the lease."""
        raise NotImplementedError

    def queue_stats(self) -> dict:
        raise NotImplementedError

    @abstractmethod
    def recover_stale_jobs(self, heartbeat_before: float) -> int: ...

    @abstractmethod
    def get_stage(self, job_id: int, stage: str) -> Optional[dict]: ...

    @abstractmethod
    def list_stages(self, job_id: int) -> List[dict]: ...

    @abstractmethod
    def set_stage(self, job_id: int, stage: str, status: str, error: Optional[str] = None, output=None,
                  owner: Optional[str] = None) -> bool: ...

    @abstractmethod
    def save_results(self, lecture_id: int, slides: List[dict], timeline: List[dict], aligned_segments: List[dict]) -> dict: ...

    @abstractmethod
    def replace_evidence(self, lecture_id: int, records: List[dict]) -> List[int]: ...

    @abstractmethod
    def add_evidence(self, lecture_id: int, source_type: str, source_id: int, text: str, start=None, end=None, slide_index=None, slide_id=None, source_asset_id=None, confidence=None) -> int: ...

    @abstractmethod
    def rebuild_index(self, lecture_id: int) -> int: ...

    @abstractmethod
    def index_evidence(self, evidence_id: int) -> None: ...

    @abstractmethod
    def get_evidence(self, evidence_id: int) -> Optional[dict]: ...

    @abstractmethod
    def list_evidence(self, lecture_id: int) -> List[dict]: ...

    @abstractmethod
    def viewer_data(self, lecture_id: int) -> dict: ...

    @abstractmethod
    def search_evidence(self, lecture_ids: Sequence[int], query: str, limit: int = 20, match: str = 'all') -> List[dict]: ...

    @abstractmethod
    def search(self, lecture_id: int, query: str, limit: int = 20) -> List[dict]: ...

    @abstractmethod
    def update_segment_text(self, lecture_id: int, segment_id: int, text: str) -> bool: ...

    @abstractmethod
    def set_segment_slide(self, lecture_id: int, segment_id: int, slide_index: Optional[int], manual: bool = True, confidence: Optional[float] = None) -> bool: ...

    @abstractmethod
    def replace_timeline(self, lecture_id: int, entries: List[dict]) -> None: ...

    @abstractmethod
    def segments_for_realign(self, lecture_id: int) -> List[dict]: ...

    @abstractmethod
    def slide_count(self, lecture_id: int) -> int: ...

    @abstractmethod
    def get_slide(self, lecture_id: int, idx: int) -> Optional[dict]: ...

    # ── Sprint 2: Notiv-generated insights + raw timeline ────────────────
    @abstractmethod
    def replace_insights(self, lecture_id: int, records: List[dict], fingerprint: str) -> List[int]: ...

    @abstractmethod
    def insight_fingerprint(self, lecture_id: int) -> Optional[str]: ...

    @abstractmethod
    def list_insights(self, lecture_id: int, kind: Optional[str] = None) -> List[dict]: ...

    @abstractmethod
    def timeline_rows(self, lecture_id: int) -> List[dict]: ...

    # ── Sprint 3: embeddings + Ask Notiv chat ────────────────────────────
    @abstractmethod
    def replace_embeddings(self, lecture_id: int, model: str, dim: int, rows: List[tuple], fingerprint: str) -> int: ...

    @abstractmethod
    def embedding_meta(self, lecture_id: int) -> Optional[dict]: ...

    @abstractmethod
    def load_embeddings(self, lecture_id: int) -> List[tuple]: ...

    @abstractmethod
    def chat_conversation(self, lecture_id: int, user_id: int, create: bool = False) -> Optional[dict]: ...

    @abstractmethod
    def chat_add_message(self, conversation_id: int, role: str, content: str, grounded: Optional[bool] = None,
                         meta: Optional[dict] = None, evidence_ids: Optional[List[int]] = None) -> int: ...

    @abstractmethod
    def chat_messages(self, conversation_id: int, limit: Optional[int] = None) -> List[dict]: ...

    @abstractmethod
    def chat_clear(self, conversation_id: int) -> int: ...

    @abstractmethod
    def chat_count_since(self, conversation_id: int, role: str, since: float) -> int: ...

    # ── Sprint 4: knowledge, study items, sessions, progress ─────────────
    @abstractmethod
    def sync_knowledge(self, lecture_id: int, records: List[dict], fingerprint: str) -> dict: ...

    @abstractmethod
    def list_knowledge(self, lecture_ids: Sequence[int], kind: Optional[str] = None) -> List[dict]: ...

    @abstractmethod
    def get_knowledge(self, item_id: int) -> Optional[dict]: ...

    @abstractmethod
    def delete_knowledge(self, item_id: int) -> bool: ...

    @abstractmethod
    def knowledge_meta(self, lecture_id: int) -> Optional[dict]: ...

    @abstractmethod
    def get_ai_study(self, lecture_id: int) -> Optional[dict]: ...

    @abstractmethod
    def save_ai_study(self, lecture_id: int, fingerprint: str, provider: str, model: str, content: dict) -> None: ...

    @abstractmethod
    def search_knowledge(self, lecture_ids: Sequence[int], query: str, limit: int = 20) -> List[dict]: ...

    @abstractmethod
    def mark_reviewed(self, user_id: int, knowledge_id: int) -> dict: ...

    @abstractmethod
    def reviewed_map(self, user_id: int, knowledge_ids: Sequence[int]) -> Dict[int, dict]: ...

    @abstractmethod
    def sync_study_items(self, lecture_id: int, records: List[dict], fingerprint: str, kinds: Optional[Sequence[str]] = None) -> dict: ...

    @abstractmethod
    def replace_item_content(self, item_id: int, record: dict) -> None: ...

    @abstractmethod
    def list_study_items(self, lecture_ids: Sequence[int], kind: Optional[str] = None, difficulty: Optional[str] = None, status: str = 'active', ids: Optional[Sequence[int]] = None) -> List[dict]: ...

    @abstractmethod
    def get_study_item(self, item_id: int, include_deleted: bool = False) -> Optional[dict]: ...

    @abstractmethod
    def update_study_item(self, item_id: int, fields: dict, links: Optional[List[dict]] = None) -> None: ...

    @abstractmethod
    def delete_study_item(self, item_id: int) -> bool: ...

    @abstractmethod
    def study_meta(self, lecture_id: int) -> Optional[dict]: ...

    @abstractmethod
    def create_session(self, user_id: int, scope_type: str, scope_id: int, config: dict, item_ids: Sequence[int]) -> int: ...

    @abstractmethod
    def get_session(self, session_id: int) -> Optional[dict]: ...

    @abstractmethod
    def session_item_ids(self, session_id: int) -> List[int]: ...

    @abstractmethod
    def add_attempt(self, session_id: int, item_id: int, user_id: int, correct: bool, self_grade: Optional[str] = None, selected_option: Optional[str] = None, response_text: Optional[str] = None) -> int: ...

    @abstractmethod
    def session_attempts(self, session_id: int) -> Dict[int, dict]: ...

    @abstractmethod
    def finish_session(self, session_id: int) -> None: ...

    @abstractmethod
    def progress_rows(self, user_id: int, item_ids: Sequence[int]) -> Dict[int, dict]: ...

    @abstractmethod
    def session_counts(self, user_id: int, scope_type: str, scope_id: int) -> dict: ...


class SqliteLectureRepository(LectureRepository):
    """Dev/test implementation. `db_path` selects the SQLite file
    (process-wide; see store.use_db)."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        store.use_db(db_path)

    def _ensure(self):
        if store.DB_PATH != self.db_path:        # another repo instance switched the file; switch back
            store.use_db(self.db_path)

    def create_workspace(self, owner_id: int, name: str) -> int:
        self._ensure()
        return store.create_workspace(owner_id, name)

    def ensure_personal_workspace(self, user_id: int) -> int:
        self._ensure()
        return store.ensure_personal_workspace(user_id)

    def add_member(self, workspace_id: int, user_id: int, role: str = 'member') -> None:
        self._ensure()
        return store.add_member(workspace_id, user_id, role)

    def is_member(self, workspace_id: int, user_id: int) -> bool:
        self._ensure()
        return store.is_member(workspace_id, user_id)

    def create_course(self, owner_id: int, name: str, workspace_id: Optional[int] = None) -> int:
        self._ensure()
        return store.create_course(owner_id, name, workspace_id)

    def get_course(self, course_id: int) -> Optional[dict]:
        self._ensure()
        return store.get_course(course_id)

    def list_courses_for_user(self, user_id: int, limit: int = 100, offset: int = 0) -> List[dict]:
        self._ensure()
        return store.list_courses_for_user(user_id, limit, offset)

    def list_lectures_for_user(self, user_id: int, limit: int = 100, offset: int = 0, course_id: Optional[int] = None) -> List[dict]:
        self._ensure()
        return store.list_lectures_for_user(user_id, limit, offset, course_id)

    def course_lecture_ids(self, course_id: int, only_done: bool = True) -> List[int]:
        self._ensure()
        return store.course_lecture_ids(course_id, only_done)

    def create_lecture(self, owner_id: int, title: str, language: Optional[str] = None, course_id: Optional[int] = None, workspace_id: Optional[int] = None) -> int:
        self._ensure()
        return store.create_lecture(owner_id, title, language, course_id, workspace_id)

    def get_lecture(self, lecture_id: int) -> Optional[dict]:
        self._ensure()
        return store.get_lecture(lecture_id)

    def set_status(self, lecture_id: int, status: str, stage: str = None, progress_pct: int = None, error: str = None, duration: float = None):
        self._ensure()
        return store.set_status(lecture_id, status, stage, progress_pct, error, duration)

    def delete_lecture_data(self, lecture_id: int) -> None:
        self._ensure()
        return store.delete_lecture_data(lecture_id)

    def add_asset(self, lecture_id: int, workspace_id: int, **f) -> int:
        self._ensure()
        return store.add_asset(lecture_id, workspace_id, **f)

    def get_asset(self, asset_id: int) -> Optional[dict]:
        self._ensure()
        return store.get_asset(asset_id)

    def list_assets(self, lecture_id: int, type: Optional[str] = None, status: Optional[str] = None) -> List[dict]:
        self._ensure()
        return store.list_assets(lecture_id, type, status)

    def update_asset(self, asset_id: int, **f) -> None:
        self._ensure()
        return store.update_asset(asset_id, **f)

    def create_job(self, lecture_id: int, workspace_id: int, type: str, max_attempts: int = 3) -> int:
        self._ensure()
        return store.create_job(lecture_id, workspace_id, type, max_attempts)

    def get_job(self, job_id: int) -> Optional[dict]:
        self._ensure()
        return store.get_job(job_id)

    def list_jobs(self, lecture_id: int) -> List[dict]:
        self._ensure()
        return store.list_jobs(lecture_id)

    def update_job(self, job_id: int, **f) -> None:
        self._ensure()
        return store.update_job(job_id, **f)

    def claim_next_job(self, now: float, job_id: Optional[int] = None, worker_id: Optional[str] = None) -> Optional[dict]:
        self._ensure()
        return store.claim_next_job(now, job_id, worker_id)

    def create_job_once(self, lecture_id: int, workspace_id: int, type: str, max_attempts: int = 3) -> tuple:
        self._ensure()
        return store.create_job_once(lecture_id, workspace_id, type, max_attempts)

    def active_job(self, lecture_id: int, type: str) -> Optional[dict]:
        self._ensure()
        return store.active_job(lecture_id, type)

    def job_owned_by(self, job_id: int, worker_id: Optional[str] = None) -> bool:
        self._ensure()
        return store.job_owned_by(job_id, worker_id)

    def beat_worker(self, worker_id: Optional[str] = None, info: Optional[dict] = None) -> None:
        self._ensure()
        return store.beat_worker(worker_id, info)

    def finalize_owned(self, job_id: int, worker_id: Optional[str], job: Optional[dict] = None,
                       lecture: Optional[dict] = None, stage: Optional[tuple] = None) -> bool:
        self._ensure()
        return store.finalize_owned(job_id, worker_id, job, lecture, stage)

    def queue_stats(self) -> dict:
        self._ensure()
        return store.queue_stats()

    def recover_stale_jobs(self, heartbeat_before: float) -> int:
        self._ensure()
        return store.recover_stale_jobs(heartbeat_before)

    def get_stage(self, job_id: int, stage: str) -> Optional[dict]:
        self._ensure()
        return store.get_stage(job_id, stage)

    def list_stages(self, job_id: int) -> List[dict]:
        self._ensure()
        return store.list_stages(job_id)

    def set_stage(self, job_id: int, stage: str, status: str, error: Optional[str] = None, output=None,
                  owner: Optional[str] = None) -> bool:
        self._ensure()
        return store.set_stage(job_id, stage, status, error, output, owner)

    def save_results(self, lecture_id: int, slides: List[dict], timeline: List[dict], aligned_segments: List[dict]) -> dict:
        self._ensure()
        return store.save_results(lecture_id, slides, timeline, aligned_segments)

    def replace_evidence(self, lecture_id: int, records: List[dict]) -> List[int]:
        self._ensure()
        return store.replace_evidence(lecture_id, records)

    def add_evidence(self, lecture_id: int, source_type: str, source_id: int, text: str, start=None, end=None, slide_index=None, slide_id=None, source_asset_id=None, confidence=None) -> int:
        self._ensure()
        return store.add_evidence(lecture_id, source_type, source_id, text, start, end, slide_index, slide_id, source_asset_id, confidence)

    def rebuild_index(self, lecture_id: int) -> int:
        self._ensure()
        return store.rebuild_index(lecture_id)

    def index_evidence(self, evidence_id: int) -> None:
        self._ensure()
        return store.index_evidence(evidence_id)

    def get_evidence(self, evidence_id: int) -> Optional[dict]:
        self._ensure()
        return store.get_evidence(evidence_id)

    def list_evidence(self, lecture_id: int) -> List[dict]:
        self._ensure()
        return store.list_evidence(lecture_id)

    def viewer_data(self, lecture_id: int) -> dict:
        self._ensure()
        return store.viewer_data(lecture_id)

    def search_evidence(self, lecture_ids: Sequence[int], query: str, limit: int = 20, match: str = 'all') -> List[dict]:
        self._ensure()
        return store.search_evidence(lecture_ids, query, limit, match)

    def search(self, lecture_id: int, query: str, limit: int = 20) -> List[dict]:
        self._ensure()
        return store.search(lecture_id, query, limit)

    def update_segment_text(self, lecture_id: int, segment_id: int, text: str) -> bool:
        self._ensure()
        return store.update_segment_text(lecture_id, segment_id, text)

    def set_segment_slide(self, lecture_id: int, segment_id: int, slide_index: Optional[int], manual: bool = True, confidence: Optional[float] = None) -> bool:
        self._ensure()
        return store.set_segment_slide(lecture_id, segment_id, slide_index, manual, confidence)

    def replace_timeline(self, lecture_id: int, entries: List[dict]) -> None:
        self._ensure()
        return store.replace_timeline(lecture_id, entries)

    def segments_for_realign(self, lecture_id: int) -> List[dict]:
        self._ensure()
        return store.segments_for_realign(lecture_id)

    def slide_count(self, lecture_id: int) -> int:
        self._ensure()
        return store.slide_count(lecture_id)

    def get_slide(self, lecture_id: int, idx: int) -> Optional[dict]:
        self._ensure()
        return store.get_slide(lecture_id, idx)

    def replace_insights(self, lecture_id: int, records: List[dict], fingerprint: str) -> List[int]:
        self._ensure()
        return store.replace_insights(lecture_id, records, fingerprint)

    def insight_fingerprint(self, lecture_id: int) -> Optional[str]:
        self._ensure()
        return store.insight_fingerprint(lecture_id)

    def list_insights(self, lecture_id: int, kind: Optional[str] = None) -> List[dict]:
        self._ensure()
        return store.list_insights(lecture_id, kind)

    def timeline_rows(self, lecture_id: int) -> List[dict]:
        self._ensure()
        return store.timeline_rows(lecture_id)

    def replace_embeddings(self, lecture_id, model, dim, rows, fingerprint):
        self._ensure()
        return store.replace_embeddings(lecture_id, model, dim, rows, fingerprint)

    def embedding_meta(self, lecture_id):
        self._ensure()
        return store.embedding_meta(lecture_id)

    def load_embeddings(self, lecture_id):
        self._ensure()
        return store.load_embeddings(lecture_id)

    def chat_conversation(self, lecture_id, user_id, create=False):
        self._ensure()
        return store.chat_conversation(lecture_id, user_id, create)

    def chat_add_message(self, conversation_id, role, content, grounded=None, meta=None, evidence_ids=None):
        self._ensure()
        return store.chat_add_message(conversation_id, role, content, grounded, meta, evidence_ids)

    def chat_messages(self, conversation_id, limit=None):
        self._ensure()
        return store.chat_messages(conversation_id, limit)

    def chat_clear(self, conversation_id):
        self._ensure()
        return store.chat_clear(conversation_id)

    def chat_count_since(self, conversation_id, role, since):
        self._ensure()
        return store.chat_count_since(conversation_id, role, since)

    def sync_knowledge(self, lecture_id: int, records: List[dict], fingerprint: str) -> dict:
        self._ensure()
        return study_store.sync_knowledge(lecture_id, records, fingerprint)

    def list_knowledge(self, lecture_ids: Sequence[int], kind: Optional[str] = None) -> List[dict]:
        self._ensure()
        return study_store.list_knowledge(lecture_ids, kind)

    def get_knowledge(self, item_id: int) -> Optional[dict]:
        self._ensure()
        return study_store.get_knowledge(item_id)

    def delete_knowledge(self, item_id: int) -> bool:
        self._ensure()
        return study_store.delete_knowledge(item_id)

    def knowledge_meta(self, lecture_id: int) -> Optional[dict]:
        self._ensure()
        return study_store.knowledge_meta(lecture_id)

    def get_ai_study(self, lecture_id: int) -> Optional[dict]:
        self._ensure()
        return study_store.get_ai_study(lecture_id)

    def save_ai_study(self, lecture_id: int, fingerprint: str, provider: str, model: str, content: dict) -> None:
        self._ensure()
        study_store.save_ai_study(lecture_id, fingerprint, provider, model, content)

    def search_knowledge(self, lecture_ids: Sequence[int], query: str, limit: int = 20) -> List[dict]:
        self._ensure()
        return study_store.search_knowledge(lecture_ids, query, limit)

    def mark_reviewed(self, user_id: int, knowledge_id: int) -> dict:
        self._ensure()
        return study_store.mark_reviewed(user_id, knowledge_id)

    def reviewed_map(self, user_id: int, knowledge_ids: Sequence[int]) -> Dict[int, dict]:
        self._ensure()
        return study_store.reviewed_map(user_id, knowledge_ids)

    def sync_study_items(self, lecture_id: int, records: List[dict], fingerprint: str, kinds: Optional[Sequence[str]] = None) -> dict:
        self._ensure()
        return study_store.sync_study_items(lecture_id, records, fingerprint, kinds)

    def replace_item_content(self, item_id: int, record: dict) -> None:
        self._ensure()
        return study_store.replace_item_content(item_id, record)

    def list_study_items(self, lecture_ids: Sequence[int], kind: Optional[str] = None, difficulty: Optional[str] = None, status: str = 'active', ids: Optional[Sequence[int]] = None) -> List[dict]:
        self._ensure()
        return study_store.list_study_items(lecture_ids, kind, difficulty, status, ids)

    def get_study_item(self, item_id: int, include_deleted: bool = False) -> Optional[dict]:
        self._ensure()
        return study_store.get_study_item(item_id, include_deleted)

    def update_study_item(self, item_id: int, fields: dict, links: Optional[List[dict]] = None) -> None:
        self._ensure()
        return study_store.update_study_item(item_id, fields, links)

    def delete_study_item(self, item_id: int) -> bool:
        self._ensure()
        return study_store.delete_study_item(item_id)

    def study_meta(self, lecture_id: int) -> Optional[dict]:
        self._ensure()
        return study_store.study_meta(lecture_id)

    def create_session(self, user_id: int, scope_type: str, scope_id: int, config: dict, item_ids: Sequence[int]) -> int:
        self._ensure()
        return study_store.create_session(user_id, scope_type, scope_id, config, item_ids)

    def get_session(self, session_id: int) -> Optional[dict]:
        self._ensure()
        return study_store.get_session(session_id)

    def session_item_ids(self, session_id: int) -> List[int]:
        self._ensure()
        return study_store.session_item_ids(session_id)

    def add_attempt(self, session_id: int, item_id: int, user_id: int, correct: bool, self_grade: Optional[str] = None, selected_option: Optional[str] = None, response_text: Optional[str] = None) -> int:
        self._ensure()
        return study_store.add_attempt(session_id, item_id, user_id, correct, self_grade, selected_option, response_text)

    def session_attempts(self, session_id: int) -> Dict[int, dict]:
        self._ensure()
        return study_store.session_attempts(session_id)

    def finish_session(self, session_id: int) -> None:
        self._ensure()
        return study_store.finish_session(session_id)

    def progress_rows(self, user_id: int, item_ids: Sequence[int]) -> Dict[int, dict]:
        self._ensure()
        return study_store.progress_rows(user_id, item_ids)

    def session_counts(self, user_id: int, scope_type: str, scope_id: int) -> dict:
        self._ensure()
        return study_store.session_counts(user_id, scope_type, scope_id)
