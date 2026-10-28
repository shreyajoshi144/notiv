"""Cleanup, retention and privacy tooling. Everything is idempotent and safe to run concurrently / repeatedly.

  process_outbox   executes recorded object deletions (lecture deleted -> prefix removed); failures stay queued
  sweep_stale_assets  uploads stuck 'pending'/'failed' past a grace period: delete object + row
  sweep_orphans    objects under lectures/<id>/ whose lecture row no longer exists (older than a grace period)
  purge_expired    idempotency keys, rate-limit windows, finished outbox rows
  delete_user_data right-to-erasure: delete the user's lectures (rows + objects via outbox) and personal rows

CLI:  python -m core.lecture.maintenance cleanup | delete-user <id>
"""
import re
import sys
import time
from typing import Optional

from . import obs, ops_store
from .storage import StorageError

log = obs.get_logger("maintenance")
STALE_ASSET_SECONDS = 24 * 3600
ORPHAN_GRACE_SECONDS = 24 * 3600
IDEMPOTENCY_TTL = 48 * 3600
_LEC_KEY = re.compile(r"^lectures/(\d+)/")


def process_outbox(storage, limit: int = 100) -> dict:
    done = failed = 0
    for row in ops_store.outbox_pending(limit):
        try:
            if row["kind"] == "prefix":
                storage.delete_prefix(row["target"])
            elif row["kind"] == "key":
                storage.delete(row["target"])
            else:
                raise StorageError(f"unknown outbox kind {row['kind']!r}")
            ops_store.outbox_done(row["id"])
            done += 1
        except Exception as e:                    # noqa: BLE001 — stays queued, retried next sweep
            ops_store.outbox_failed(row["id"], obs.public_error(e))
            failed += 1
            obs.log_event(log, "outbox_delete_failed", level=40, outbox_id=row["id"], error=obs.public_error(e))
    obs.inc("notiv_outbox_processed_total", done, help="storage deletions completed")
    return {"done": done, "failed": failed}


def sweep_stale_assets(repo, storage, now: Optional[float] = None) -> int:
    now = now or time.time()
    n = 0
    for a in ops_store.stale_pending_assets(now - STALE_ASSET_SECONDS):
        try:
            storage.delete(a["storage_key"])
            ops_store.delete_asset_row(a["id"])
            n += 1
        except Exception as e:                    # noqa: BLE001
            obs.log_event(log, "stale_asset_cleanup_failed", level=40, asset_id=a["id"], error=obs.public_error(e))
    return n


def sweep_orphans(storage, now: Optional[float] = None, dry_run: bool = False) -> list:
    """Returns the keys removed (or that WOULD be removed with dry_run). Objects newer than the grace period are never
    touched: an upload may be in flight before its lecture row is visible/committed."""
    now = now or time.time()
    known = ops_store.known_lecture_ids()
    out = []
    for key, mtime in storage.iter_objects("lectures"):
        m = _LEC_KEY.match(key)
        if not m or int(m.group(1)) in known or now - mtime < ORPHAN_GRACE_SECONDS:
            continue
        out.append(key)
        if not dry_run:
            try:
                storage.delete(key)
            except Exception as e:                # noqa: BLE001
                obs.log_event(log, "orphan_delete_failed", level=40, error=obs.public_error(e))
    return out


def purge_expired(now: Optional[float] = None) -> dict:
    now = now or time.time()
    return {"idempotency": ops_store.idem_purge(now - IDEMPOTENCY_TTL),
            "rate_limits": ops_store.rl_purge(int(now) - 7200),
            "outbox": ops_store.outbox_purge(now - 7 * 86400)}


def run_cleanup(repo, storage) -> dict:
    res = {"outbox": process_outbox(storage), "stale_assets": sweep_stale_assets(repo, storage),
           "orphans": len(sweep_orphans(storage)), "purged": purge_expired()}
    obs.log_event(log, "cleanup_done", **{k: v for k, v in res.items()})
    return res


def delete_user_data(repo, storage, user_id: int) -> dict:
    """Right to erasure for one user. Lectures they OWN are deleted entirely; shared-workspace data other people
    own is untouched. Object deletion goes through the outbox (retried until it succeeds)."""
    lectures = ops_store.user_lecture_ids(user_id)
    for lid in lectures:
        repo.delete_lecture_data(lid)
    personal = ops_store.purge_user_rows(user_id)
    ob = process_outbox(storage, limit=max(100, len(lectures) + 10))
    ops_store.audit(None, "user.erased", "user", user_id, detail={"lectures": len(lectures)})
    obs.log_event(log, "user_erased", lectures=len(lectures))
    return {"lectures_deleted": len(lectures), **personal, "outbox": ob}


def main(argv=None) -> int:
    from . import config
    obs.setup_logging()
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] == "cleanup":
        print(run_cleanup(config.get_repo(), config.get_storage()))
        return 0
    if argv[0] == "delete-user" and len(argv) == 2 and argv[1].isdigit():
        print(delete_user_data(config.get_repo(), config.get_storage(), int(argv[1])))
        return 0
    print("usage: maintenance cleanup | delete-user <id>")
    return 2


if __name__ == "__main__":
    sys.exit(main())
