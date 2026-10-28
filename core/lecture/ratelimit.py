"""Fixed-window rate limiting. Policies are (max_requests, window_seconds), overridable via NOTIV_RL_<NAME>=max/seconds.

Backends: "memory" (default; per process — fine for one pod / tests) or "db" (NOTIV_RATELIMIT_BACKEND=db; shared across
pods via the rate_limits table). FAIL-OPEN: if the limiter itself errors, requests are allowed and the error is logged
(an outage of the limiter must not become an outage of the product); security-critical auth throttling therefore also
has the in-memory layer in front.
"""
import os
import threading
import time
from typing import Dict, Optional, Tuple

from . import obs, settings
from .errors import LimiterUnavailable, RateLimitError

POLICIES: Dict[str, Tuple[int, int]] = {
    "read": (1200, 60), "write": (300, 60), "upload": (30, 3600), "process": (30, 60),
    "ask": (120, 60), "study": (300, 60), "auth_fail": (200, 300), "anon": (240, 60),
}
log = obs.get_logger("ratelimit")
_lock = threading.Lock()
_mem: Dict[Tuple[str, int], int] = {}


def policy(name: str) -> Tuple[int, int]:
    raw = os.environ.get(f"NOTIV_RL_{name.upper()}", "")
    if raw:
        try:
            m, w = raw.split("/")
            return max(1, int(m)), max(1, int(w))
        except ValueError:
            obs.log_event(log, "bad_rate_limit_override", level=30, policy=name)
    return POLICIES.get(name, POLICIES["read"])


def enabled() -> bool:
    return settings.env("NOTIV_RATELIMIT", "on").lower() not in ("off", "0", "false")


def reset() -> None:
    with _lock:
        _mem.clear()


def _count_memory(bucket: str, window: int, now: float) -> int:
    start = int(now // window) * window
    with _lock:
        for k in [k for k in _mem if k[1] < start - window]:      # drop expired windows
            del _mem[k]
        _mem[(bucket, start)] = _mem.get((bucket, start), 0) + 1
        return _mem[(bucket, start)]


def _backend() -> str:
    return settings.env("NOTIV_RATELIMIT_BACKEND", "memory").lower()


def on_error_mode() -> str:
    """What happens when the shared (db) limiter store is unreachable. EXPLICIT, never silent:
      local (default) - keep limiting with PER-PROCESS counters. Each replica enforces alone, so the effective global
                        limit is up to N x the configured one. Reported as DEGRADED in /readyz, logs and metrics.
      deny            - refuse rate-limited requests with 503 + Retry-After (fail closed)."""
    m = settings.env("NOTIV_RATELIMIT_ON_ERROR", "local").lower()
    return m if m in ("local", "deny") else "local"


# degradation bookkeeping (per process)
_state = {"last_error_at": 0.0, "last_ok_at": 0.0, "errors": 0, "last_error": ""}
DEGRADED_WINDOW = 60.0


def degraded(now: Optional[float] = None) -> bool:
    """True while the shared backend is failing: global enforcement is NOT in effect."""
    if _backend() != "db":
        return False
    now = time.time() if now is None else now
    return _state["last_error_at"] > _state["last_ok_at"] and now - _state["last_error_at"] < DEGRADED_WINDOW


def status() -> dict:
    be = _backend()
    if be != "db":
        return {"backend": be, "scope": "per-process", "degraded": False, "on_error": None}
    d = degraded()
    return {"backend": "db", "scope": "per-process (DEGRADED: shared store unreachable)" if d else "global",
            "degraded": d, "on_error": on_error_mode(), "errors": _state["errors"], "last_error": _state["last_error"]}


def reset_state() -> None:
    _state.update(last_error_at=0.0, last_ok_at=0.0, errors=0, last_error="")


def _count(bucket: str, window: int, now: float) -> int:
    start = int(now // window) * window
    if _backend() != "db":
        return _count_memory(bucket, window, now)
    from . import ops_store
    try:
        n = ops_store.rl_hit(f"{bucket}|{window}", start)
    except Exception as e:                                  # noqa: BLE001
        _state.update(last_error_at=time.time(), errors=_state["errors"] + 1, last_error=type(e).__name__)
        obs.inc("notiv_ratelimit_backend_errors_total", help="shared rate-limit store failures", mode=on_error_mode())
        obs.log_event(log, "limiter_backend_unavailable", level=40, mode=on_error_mode(), error=obs.public_error(e),
                      note="global rate-limit enforcement NOT in effect")
        if on_error_mode() == "deny":
            raise LimiterUnavailable("rate limiter unavailable", 5) from e
        return _count_memory(bucket, window, now)           # explicit, flagged degradation: per-process counting
    _state["last_ok_at"] = time.time()
    return n


def check(name: str, subject, now: Optional[float] = None) -> None:
    """Counts one hit for `subject` (user id / IP) under policy `name`; raises RateLimitError over the limit.
    Backend outage: see on_error_mode() — never silently unlimited. LimiterUnavailable (503) in 'deny' mode."""
    if not enabled():
        return
    mx, window = policy(name)
    now = time.time() if now is None else now
    try:
        n = _count(f"{name}:{subject}", window, now)
    except LimiterUnavailable:
        raise
    except Exception as e:                                  # noqa: BLE001 — unexpected limiter bug: log loudly, local count
        obs.log_event(log, "limiter_error", level=40, error=obs.public_error(e))
        n = _count_memory(f"{name}:{subject}", window, now)
    if n > mx:
        obs.inc("notiv_rate_limited_total", help="requests rejected by rate limits", policy=name)
        retry = int(window - (now % window)) or 1
        raise RateLimitError("too many requests", retry)
