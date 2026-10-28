"""Liveness vs readiness. /healthz = process is up (no dependencies). /readyz = this instance can serve traffic:
database reachable AND migrated to head, storage reachable, auth configured. The job queue is reported for
dashboards but does NOT gate readiness (a backed-up queue is not a reason to stop routing requests)."""
import time

from . import config, migrations, settings

_priv_cache = {"at": 0.0, "val": None, "key": None}


def _storage_private(storage) -> dict:
    """Production readiness requires a PRIVATE bucket: positive proof, not absence of evidence. Result cached 60 s."""
    if not hasattr(storage, "check_private"):
        return {"ok": True, "detail": "n/a (local storage)"}
    key = id(storage)
    if _priv_cache["key"] == key and time.time() - _priv_cache["at"] < 60 and _priv_cache["val"]:
        r = _priv_cache["val"]
    else:
        try:
            r = storage.check_private()
        except Exception as e:                        # noqa: BLE001
            r = {"private": None, "detail": f"privacy check failed ({type(e).__name__})"}
        _priv_cache.update(at=time.time(), val=r, key=key)
    if r["private"] is False:
        return {"ok": False, "detail": "bucket is NOT private: " + r["detail"]}
    if r["private"] is None:
        if settings.env_bool("NOTIV_S3_SKIP_PRIVACY_CHECK", False) and not settings.is_production():
            return {"ok": True, "detail": "UNVERIFIED (skipped, non-production): " + r["detail"]}
        return {"ok": not settings.is_production(), "detail": "privacy could not be verified: " + r["detail"]}
    return {"ok": True, "detail": "private: " + r["detail"]}


def readiness() -> dict:
    checks, ok = {}, True

    try:
        from . import store
        c = store.conn()
        st = migrations.status(c, store.DIALECT)
        checks["database"] = {"ok": st["ok"], "detail": f"schema {st['current']}/{st['head']}" + ("" if st["ok"] else " (not at head)")}
    except Exception as e:                        # noqa: BLE001
        checks["database"] = {"ok": False, "detail": f"unreachable ({type(e).__name__})"}
    try:
        checks["storage"] = config.get_storage().health()
    except Exception as e:                        # noqa: BLE001
        checks["storage"] = {"ok": False, "detail": f"unavailable ({type(e).__name__})"}
    if checks["storage"].get("ok"):
        try:
            checks["storage_private"] = _storage_private(config.get_storage())
        except Exception as e:                        # noqa: BLE001
            checks["storage_private"] = {"ok": False, "detail": f"unavailable ({type(e).__name__})"}
    from . import ratelimit
    rl = ratelimit.status()
    checks["rate_limit"] = {"ok": not rl["degraded"], "detail": (f"backend={rl['backend']} scope={rl['scope']}" +
                            (f" on_error={rl['on_error']} — global enforcement NOT in effect" if rl["degraded"] else ""))}
    try:
        from . import retrieval
        rs = retrieval.retrieval_status()
        checks["retrieval"] = {"ok": not (rs["problems"] and settings.is_production()), "detail":
                               f"kind={rs['kind']} (configured={rs['configured']})" + ("" if rs["kind"] == "semantic" else " — NOT semantic: keyword/sub-word matching only") +
                               (f"; DEGRADED: {'; '.join(rs['problems']) or 'provider failing (' + str(rs['last_failure']) + ')'}" if rs["degraded"] else "")}
    except Exception as e:                        # noqa: BLE001
        checks["retrieval"] = {"ok": False, "detail": f"unavailable ({type(e).__name__})"}
    try:
        from . import media
        ps = media.processing_status()
        miss = ps["missing_tools"] + ps["missing_packages"]
        # informational (an API-only replica need not have them) but visible: uploads cannot be processed without these
        detail = "all present" if not miss else ("MISSING on this host: " + ", ".join(miss) + " — cannot process: " +
                                                 ", ".join(f.upper() for f in ps["blocked_formats"]))
        if not ps["ocr_available"]:
            detail += "; OCR unavailable (tesseract not installed or NOTIV_OCR=off): scanned pages will have no text"
        checks["processing_tools"] = {"ok": True, "detail": detail, **ps}
    except Exception as e:                        # noqa: BLE001
        checks["processing_tools"] = {"ok": True, "detail": f"unknown ({type(e).__name__})"}
    try:
        from . import llm
        ls = llm.status()
        detail = ("active: " + " → ".join(ls["active"])) if ls["active"] else \
            "NONE configured — Ask Notiv answers with lecture quotes only; AI quizzes are unavailable"
        if ls["problems"]:
            detail += "; " + "; ".join(ls["problems"])
        # informational: Ask degrades to verbatim quotes without a model, so this never gates readiness
        checks["answer_model"] = {"ok": True, "detail": detail, "order": ls["order"], "active": ls["active"],
                                  "providers": [{k: r[k] for k in ("provider", "model", "configured", "installed")} for r in ls["providers"]]}
    except Exception as e:                        # noqa: BLE001
        checks["answer_model"] = {"ok": True, "detail": f"unknown ({type(e).__name__})"}
    sec = settings.env("NOTIV_AUTH_SECRET", "")
    checks["auth"] = {"ok": bool(sec) and len(sec.encode()) >= settings.MIN_SECRET_BYTES,
                      "detail": "configured" if sec else "NOTIV_AUTH_SECRET not set"}
    probs = settings.problems("api")
    checks["config"] = {"ok": not probs, "detail": "ok" if not probs else f"{len(probs)} problem(s)"}
    ok = all(v["ok"] for v in checks.values())
    try:
        queue = config.get_repo().queue_stats()
    except Exception:                             # noqa: BLE001
        queue = None
    return {"status": "ready" if ok else "not_ready", "checks": checks, "queue": queue}
