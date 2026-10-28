"""Observability primitives (stdlib only): structured JSON logs with a per-request id, secret/path redaction,
and in-process Prometheus-format metrics.

Nothing here imports the rest of the app, so every layer (worker, storage, HTTP) can use it.
Logging never raises into the caller; metrics are per-process (aggregate across processes in Prometheus).
"""
import contextvars
import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from typing import Dict, Tuple

request_id_var: contextvars.ContextVar = contextvars.ContextVar("notiv_request_id", default="-")

_SECRET_KEYS = re.compile(r"(pass(word)?|secret|token|authorization|api[_-]?key|signature|^sig$|cookie|credential)", re.I)
_REDACT_PATTERNS = [
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)([?&](?:sig|signature|token|x-amz-signature|x-amz-credential|x-amz-security-token)=)[^&\s]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b(postgres(?:ql)?(?:\+\w+)?://[^:/\s]+:)[^@\s]+@"), r"\1[REDACTED]@"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "[JWT]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[AWSKEY]"),
    # absolute filesystem paths (server internals must not reach clients or job rows)
    (re.compile(r"(?<![\w:/.])/(?:home|root|tmp|var|usr|opt|srv|mnt|etc|app|data|Users|private)/[^\s'\"),;:]+"), "[path]"),
    (re.compile(r"\b[A-Za-z]:\\[^\s'\"),;]+"), "[path]"),
]


def redact(text: str) -> str:
    for rx, rep in _REDACT_PATTERNS:
        text = rx.sub(rep, text)
    return text


def redact_obj(v, _key: str = "", depth: int = 0):
    if depth > 6:
        return "[deep]"
    if _key and _SECRET_KEYS.search(_key):
        return "[REDACTED]"
    if isinstance(v, str):
        return redact(v)[:2000]
    if isinstance(v, dict):
        return {str(k): redact_obj(x, str(k), depth + 1) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [redact_obj(x, "", depth + 1) for x in list(v)[:50]]
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    return redact(str(v))[:500]


def public_error(exc: BaseException, limit: int = 300) -> str:
    """The text that may be stored on a job/lecture row and shown to the user: class + message with paths,
    credentials and signed-URL parameters removed. Full detail goes to the (redacted) log only."""
    return f"{type(exc).__name__}: {redact(str(exc))}"[:limit]


# ── request id ───────────────────────────────────────────────────────────
_RID_OK = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def new_request_id(incoming: str = "") -> str:
    """Accepts a well-formed upstream id (log correlation) but never an arbitrary string (log injection)."""
    return incoming if incoming and _RID_OK.match(incoming) else uuid.uuid4().hex


# ── logging ──────────────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {"ts": round(record.created, 3), "level": record.levelname, "logger": record.name,
             "msg": redact(record.getMessage()), "request_id": request_id_var.get()}
        extra = getattr(record, "fields", None)
        if extra:
            d.update(redact_obj(extra))
        if record.exc_info:
            d["exc"] = redact("".join(__import__("traceback").format_exception(*record.exc_info)))[-3000:]
        try:
            return json.dumps(d, default=str, separators=(",", ":"))
        except Exception:                                  # noqa: BLE001 — logging must not raise
            return json.dumps({"level": "ERROR", "msg": "unserialisable log record"})


_configured = False


def setup_logging(level: str = "", json_logs=None) -> None:
    global _configured
    level = (level or os.getenv("NOTIV_LOG_LEVEL", "INFO")).upper()
    if json_logs is None:
        json_logs = os.getenv("NOTIV_LOG_FORMAT", "json").lower() == "json"
    root = logging.getLogger("notiv")
    root.setLevel(level)
    if not _configured:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(JsonFormatter() if json_logs else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.handlers[:] = [h]
        root.propagate = False
        _configured = True


logging.getLogger("notiv").addHandler(logging.NullHandler())     # silent until setup_logging() (API/worker call it)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"notiv.{name}")


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields) -> None:
    try:
        logger.log(level, event, extra={"fields": fields})
    except Exception:                                      # noqa: BLE001
        pass


# ── metrics (Prometheus text format) ─────────────────────────────────────
_lock = threading.Lock()
_counters: Dict[Tuple[str, Tuple], float] = {}
_gauges: Dict[Tuple[str, Tuple], float] = {}
_hist: Dict[Tuple[str, Tuple], list] = {}
_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 300, 1800)
_HELP: Dict[str, str] = {}


def _lk(labels: dict) -> Tuple:
    return tuple(sorted((k, str(v)) for k, v in labels.items()))


def inc(name: str, value: float = 1.0, help: str = "", **labels) -> None:
    with _lock:
        k = (name, _lk(labels))
        _counters[k] = _counters.get(k, 0.0) + value
        if help:
            _HELP[name] = help


def gauge(name: str, value: float, help: str = "", **labels) -> None:
    with _lock:
        _gauges[(name, _lk(labels))] = float(value)
        if help:
            _HELP[name] = help


def observe(name: str, value: float, help: str = "", **labels) -> None:
    with _lock:
        h = _hist.setdefault((name, _lk(labels)), [[0] * len(_BUCKETS), 0, 0.0])
        for i, b in enumerate(_BUCKETS):
            if value <= b:
                h[0][i] += 1
        h[1] += 1
        h[2] += value
        if help:
            _HELP[name] = help


def counter_value(name: str, **labels) -> float:
    with _lock:
        return _counters.get((name, _lk(labels)), 0.0)


def reset_metrics() -> None:
    with _lock:
        _counters.clear(); _gauges.clear(); _hist.clear()


def _fmt(labels: Tuple, extra: str = "") -> str:
    parts = [f'{k}="{str(v).replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"' for k, v in labels]
    if extra:
        parts.append(extra)
    return "{" + ",".join(parts) + "}" if parts else ""


def render_prometheus() -> str:
    out = []
    with _lock:
        for kind, store in (("counter", _counters), ("gauge", _gauges)):
            for name in sorted({n for n, _ in store}):
                out.append(f"# HELP {name} {_HELP.get(name, name)}")
                out.append(f"# TYPE {name} {kind}")
                for (n, lab), v in sorted(store.items()):
                    if n == name:
                        out.append(f"{n}{_fmt(lab)} {v:g}")
        for name in sorted({n for n, _ in _hist}):
            out.append(f"# HELP {name} {_HELP.get(name, name)}")
            out.append(f"# TYPE {name} histogram")
            for (n, lab), (bk, cnt, total) in sorted(_hist.items()):
                if n != name:
                    continue
                for b, c in zip(_BUCKETS, bk):
                    le = 'le="%g"' % b
                    out.append(f"{n}_bucket{_fmt(lab, le)} {c}")
                inf = 'le="+Inf"'
                out.append(f"{n}_bucket{_fmt(lab, inf)} {cnt}")
                out.append(f"{n}_sum{_fmt(lab)} {total:g}")
                out.append(f"{n}_count{_fmt(lab)} {cnt}")
    return "\n".join(out) + "\n"


class timer:
    """with obs.timer("name", stage="x") as t: ...  -> observes elapsed seconds; t.elapsed available after."""

    def __init__(self, name: str, **labels):
        self.name, self.labels, self.elapsed = name, labels, 0.0

    def __enter__(self):
        self._t = time.perf_counter()
        return self

    def __exit__(self, *a):
        self.elapsed = time.perf_counter() - self._t
        observe(self.name, self.elapsed, **self.labels)
