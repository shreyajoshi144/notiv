"""Production-safe configuration.

* Secrets can come from the environment OR a file (`NAME_FILE=/run/secrets/name`, Docker/K8s secrets).
* `NOTIV_ENV=production` turns on fail-closed validation: the process refuses to start (validate_or_raise)
  with weak/missing secrets, SQLite, local object storage, wildcard CORS, or an unauthenticated /metrics.
* Nothing here logs secret VALUES; problems name the variable only.

Roles: "api" (HTTP server) and "worker" need different subsets, e.g. a worker needs no CORS.
"""
import os
from typing import List, Optional

from .errors import ValidationError

PLACEHOLDERS = {"changeme", "change-me", "secret", "password", "notiv", "please-change-me", "dev-secret", "example"}
MIN_SECRET_BYTES = 32


def env(name: str, default: str = "") -> str:
    """os.getenv with `<NAME>_FILE` support (file wins; trailing newline stripped)."""
    f = os.environ.get(name + "_FILE")
    if f:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError as e:
            raise ValidationError(f"{name}_FILE is set but unreadable ({type(e).__name__})")
    return os.environ.get(name, default)


def env_int(name: str, default: int, lo: Optional[int] = None, hi: Optional[int] = None) -> int:
    raw = env(name, "")
    if raw == "":
        return default
    try:
        v = int(raw)
    except ValueError:
        raise ValidationError(f"{name} must be an integer")
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise ValidationError(f"{name} out of range [{lo}, {hi}]")
    return v


def env_bool(name: str, default: bool = False) -> bool:
    raw = env(name, "")
    return default if raw == "" else raw.strip().lower() in ("1", "true", "yes", "on")


def is_production() -> bool:
    return env("NOTIV_ENV", "development").strip().lower() in ("production", "prod")


def database_url() -> str:
    return env("NOTIV_DATABASE_URL", "")


def is_postgres_url(url: str) -> bool:
    return url.lower().startswith(("postgres://", "postgresql://", "postgresql+psycopg://"))


def _weak(secret: str) -> bool:
    s = secret.strip().lower()
    return len(secret.encode()) < MIN_SECRET_BYTES or s in PLACEHOLDERS or len(set(secret)) < 8


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(env(name, str(default)) or default))
    except ValueError:
        return default


def problems(role: str = "api") -> List[str]:
    """All configuration problems (fatal ones only in production). Never includes secret values."""
    prod, out = is_production(), []

    def bad(msg: str, fatal_in_prod: bool = True):
        if prod or not fatal_in_prod:
            out.append(msg)

    # auth
    if role == "api":
        sec = env("NOTIV_AUTH_SECRET", "")
        if not sec:
            bad("NOTIV_AUTH_SECRET is not set")
        elif _weak(sec):
            bad(f"NOTIV_AUTH_SECRET is weak (need >= {MIN_SECRET_BYTES} bytes, not a placeholder, varied characters)")
        for prev in [x for x in env("NOTIV_AUTH_SECRET_PREVIOUS", "").split(",") if x.strip()]:
            if _weak(prev.strip()):
                bad("NOTIV_AUTH_SECRET_PREVIOUS contains a weak secret")
    # database
    url = database_url()
    if url and not is_postgres_url(url):
        bad("NOTIV_DATABASE_URL must be a postgresql:// URL", fatal_in_prod=False)
    if prod and not url and not env_bool("NOTIV_ALLOW_SQLITE_IN_PROD"):
        out.append("production requires NOTIV_DATABASE_URL (PostgreSQL); SQLite is development-only "
                   "(override: NOTIV_ALLOW_SQLITE_IN_PROD=1)")
    # storage
    kind = env("NOTIV_STORAGE", "local")
    if kind not in ("local", "s3"):
        out.append("NOTIV_STORAGE must be 'local' or 's3'")
    if kind == "s3":
        if not env("NOTIV_S3_BUCKET"):
            out.append("NOTIV_S3_BUCKET is required when NOTIV_STORAGE=s3")
    else:
        if prod and not env_bool("NOTIV_ALLOW_LOCAL_STORAGE_IN_PROD"):
            out.append("production requires NOTIV_STORAGE=s3 (override: NOTIV_ALLOW_LOCAL_STORAGE_IN_PROD=1)")
        sig = env("NOTIV_SIGNING_SECRET", "")
        if role == "api" and (prod or sig) and (not sig or _weak(sig)):
            bad("NOTIV_SIGNING_SECRET is missing or weak (needed to sign local file URLs)")
        if role == "api" and sig and env("NOTIV_AUTH_SECRET", "") == sig:
            bad("NOTIV_SIGNING_SECRET must differ from NOTIV_AUTH_SECRET")
    # rate limiting: per-process memory counters are NOT enforced across replicas/processes, so production must either
    # use the shared (database) backend or explicitly declare — and be constrained to — a single process.
    if role == "api" and prod:
        backend = env("NOTIV_RATELIMIT_BACKEND", "memory").lower()
        single = env_bool("NOTIV_SINGLE_INSTANCE", False)
        procs = _int_env("NOTIV_REPLICAS", 1) * max(_int_env("WEB_CONCURRENCY", 1), _int_env("NOTIV_WEB_WORKERS", 1))
        if env("NOTIV_RATELIMIT", "on").lower() in ("off", "0", "false"):
            out.append("NOTIV_RATELIMIT must not be disabled in production")
        if backend not in ("memory", "db"):
            out.append("NOTIV_RATELIMIT_BACKEND must be 'memory' or 'db'")
        elif backend != "db":
            if not single:
                out.append("production requires NOTIV_RATELIMIT_BACKEND=db (limits shared by all replicas) or an explicit "
                           "NOTIV_SINGLE_INSTANCE=1 (one API process only)")
            elif procs > 1:
                out.append(f"NOTIV_SINGLE_INSTANCE=1 contradicts NOTIV_REPLICAS/WEB_CONCURRENCY ({procs} processes): "
                           "in-memory rate limits would be per-process; use NOTIV_RATELIMIT_BACKEND=db")
    # retrieval provider: a typo must be loud; a missing neural provider is fatal in production, a warning elsewhere
    from . import retrieval as _r
    ecfg = _r.embedding_config()
    for pmsg in ecfg["problems"]:
        if "must be one of" in pmsg:
            out.append(pmsg)
        else:
            bad(pmsg)
    # http surface
    if role == "api":
        origins = [o.strip() for o in env("NOTIV_CORS_ORIGINS", "").split(",") if o.strip()]
        if "*" in origins:
            bad("NOTIV_CORS_ORIGINS must not contain '*'")
        if env_bool("NOTIV_METRICS_ENABLED", False) and not env("NOTIV_METRICS_TOKEN"):
            bad("NOTIV_METRICS_TOKEN is required when NOTIV_METRICS_ENABLED=1")
        if env_bool("NOTIV_DOCS_ENABLED", False):
            bad("NOTIV_DOCS_ENABLED must be off in production")
    return out


def validate_or_raise(role: str = "api") -> None:
    p = problems(role)
    if p:
        raise ValidationError("invalid configuration: " + "; ".join(p))
