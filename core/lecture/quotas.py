"""Per-user quotas (defaults overridable with NOTIV_QUOTA_<NAME>; <=0 disables a quota). Checked BEFORE the work is
accepted, and counted from the database (not a cache), so they hold across processes."""
import time

from . import ops_store, settings
from .errors import QuotaError

DEFAULTS = {"LECTURES": 500, "STORAGE_MB": 20 * 1024, "ASSETS_PER_LECTURE": 20, "UPLOADS_PER_DAY": 100,
            "ACTIVE_JOBS": 5, "ASKS_PER_DAY": 500}


def limit(name: str) -> int:
    return settings.env_int(f"NOTIV_QUOTA_{name}", DEFAULTS[name])


def _over(name: str, used: int, add: int = 1) -> bool:
    lim = limit(name)
    return lim > 0 and used + add > lim


def check_create_lecture(user_id: int) -> None:
    if _over("LECTURES", ops_store.count_lectures(user_id)):
        raise QuotaError("lecture quota reached", "lectures", limit("LECTURES"))


def check_upload(user_id: int, lecture_id: int, size_bytes: int) -> None:
    if _over("ASSETS_PER_LECTURE", ops_store.count_assets(lecture_id)):
        raise QuotaError("too many files in this lecture", "assets_per_lecture", limit("ASSETS_PER_LECTURE"))
    if _over("UPLOADS_PER_DAY", ops_store.uploads_since(user_id, time.time() - 86400)):
        raise QuotaError("daily upload quota reached", "uploads_per_day", limit("UPLOADS_PER_DAY"))
    lim = limit("STORAGE_MB")
    if lim > 0 and ops_store.storage_bytes(user_id) + size_bytes > lim * 1024 * 1024:
        raise QuotaError("storage quota reached", "storage", lim)


def check_process(user_id: int) -> None:
    if _over("ACTIVE_JOBS", ops_store.active_jobs_for_user(user_id)):
        raise QuotaError("too many lectures are processing; wait for one to finish", "active_jobs", limit("ACTIVE_JOBS"))


def check_ask(user_id: int) -> None:
    if _over("ASKS_PER_DAY", ops_store.asks_since(user_id, time.time() - 86400)):
        raise QuotaError("daily question quota reached", "asks_per_day", limit("ASKS_PER_DAY"))
