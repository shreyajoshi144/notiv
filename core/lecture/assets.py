"""Asset ingestion: validate -> store under a server-generated key -> record.

Storage keys never contain user-supplied names:
    lectures/<lecture_id>/assets/<uuid><allow-listed ext>
"""
import os
import uuid
from typing import Optional

from . import authz, obs, ops_store, quotas, validation
from .errors import ValidationError
from .repo import LectureRepository
from .storage import StorageService


class AssetService:
    def __init__(self, repo: LectureRepository, storage: StorageService):
        self.repo, self.storage = repo, storage

    def add_from_file(self, user_id: int, lecture_id: int, path: str, filename: str,
                      declared_mime: str = "") -> dict:
        """`path` is a local temp file the caller already wrote (size-capped).
        Raises ValidationError (nothing is stored) if the content isn't what
        its extension/MIME claims."""
        lec = authz.get_lecture(self.repo, user_id, lecture_id)
        v = validation.validate_file(path, filename, declared_mime)
        getattr(self.repo, "_ensure", lambda: None)()
        quotas.check_upload(user_id, lecture_id, v.size_bytes)
        key = f"lectures/{lecture_id}/assets/{uuid.uuid4().hex}{v.ext}"
        aid = self.repo.add_asset(lecture_id, lec["workspace_id"], uploader_id=user_id, type=v.type,
                                  storage_key=key, original_filename=validation.safe_filename(filename),
                                  mime_type=v.mime_type, size_bytes=v.size_bytes, checksum=v.checksum,
                                  duration_seconds=v.duration_seconds, status="pending")
        try:
            self.storage.put(key, path)
        except Exception:
            self.repo.update_asset(aid, status="failed")
            raise
        self.repo.update_asset(aid, status="ready")
        ops_store.audit(user_id, "asset.upload", "asset", aid, lec["workspace_id"],
                        detail={"lecture_id": lecture_id, "type": v.type, "bytes": v.size_bytes})
        return self.repo.get_asset(aid)

    def list(self, user_id: int, lecture_id: int):
        authz.get_lecture(self.repo, user_id, lecture_id)
        return self.repo.list_assets(lecture_id)

    def signed_url(self, user_id: int, asset_id: int, expires_in: int = 900) -> str:
        a = authz.get_asset(self.repo, user_id, asset_id)
        return self.storage.signed_url(a["storage_key"], expires_in)

    def delete_lecture(self, user_id: int, lecture_id: int) -> None:
        """Deletes a lecture and ALL derived data: DB rows, original assets,
        rendered slides, audio, everything under its storage prefix."""
        lec = authz.get_lecture(self.repo, user_id, lecture_id)
        # DB rows + an outbox row commit atomically; objects are then removed (and retried by maintenance if the
        # storage call fails) — never "objects gone but rows left" nor "rows gone but objects orphaned forever".
        self.repo.delete_lecture_data(lecture_id)
        ops_store.audit(user_id, "lecture.delete", "lecture", lecture_id, lec["workspace_id"])
        from . import maintenance
        maintenance.process_outbox(self.storage)
