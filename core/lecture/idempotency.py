"""Idempotency-Key support for non-idempotent POSTs (create lecture/course, upload, process).

Same (user, scope, key) + same payload  -> the stored response is REPLAYED, the work is not repeated.
Same key + different payload             -> ConflictError(idempotency_key_reused)
Same key while the first is still running -> ConflictError(idempotency_in_progress)
Handler raised                           -> key is released so the client may retry with the same key.
Keys are per-user (never shared across users) and expire (maintenance purges them).
"""
import hashlib
import json
import re
from typing import Any, Callable, Optional, Tuple

from . import ops_store
from .errors import ConflictError, ValidationError

_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


def validate_key(key: Optional[str]) -> Optional[str]:
    if key is None or key == "":
        return None
    if not _KEY.match(key):
        raise ValidationError("Idempotency-Key must be 8-128 chars of [A-Za-z0-9._:-]")
    return key


def run(user_id: int, scope: str, key: Optional[str], payload: Any, fn: Callable[[], Tuple[int, Any]]) -> Tuple[int, Any, bool]:
    """fn() -> (status, body). Returns (status, body, replayed)."""
    key = validate_key(key)
    if key is None:
        status, body = fn()
        return status, body, False
    h = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    st = ops_store.idem_begin(user_id, scope, key, h)
    if st["state"] == "replay":
        return st["status"], st["body"], True
    if st["state"] == "conflict":
        raise ConflictError("Idempotency-Key was already used with a different request", "idempotency_key_reused")
    if st["state"] == "in_progress":
        raise ConflictError("a request with this Idempotency-Key is still in progress", "idempotency_in_progress")
    try:
        status, body = fn()
    except BaseException:
        ops_store.idem_abandon(user_id, scope, key)
        raise
    ops_store.idem_finish(user_id, scope, key, status, body)
    return status, body, False
