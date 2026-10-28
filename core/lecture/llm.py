"""One answer-model layer for every AI feature (Ask Notiv, general-knowledge follow-ups, AI practice quiz, AI study
material, page transcription), with ordered fallback across providers.

    NOTIV_LLM_PROVIDERS=openai,groq,mistral,anthropic   order to try; providers without credentials are skipped
    OPENAI_API_KEY      NOTIV_OPENAI_MODEL (or OPENAI_MODEL)=gpt-6-astra   NOTIV_OPENAI_BASE_URL (or OPENAI_BASE_URL)
    GROQ_API_KEY        NOTIV_GROQ_MODEL=openai/gpt-oss-120b      NOTIV_GROQ_VISION_MODEL=qwen/qwen3.8-27b
    MISTRAL_API_KEY     NOTIV_MISTRAL_MODEL=mistral-large-latest
    ANTHROPIC_API_KEY   NOTIV_ANTHROPIC_MODEL=claude-opus-5-5   NOTIV_ANTHROPIC_EFFORT=low
    LOVABLE_API_KEY     NOTIV_LOVABLE_MODEL=openai/gpt-6-astra  (OpenAI-compatible gateway; opt-in by listing "lovable")
    NOTIV_VISION_PROVIDERS=openai,groq,anthropic        reading page images (handwritten / scanned notes)
    NOTIV_LLM_TIMEOUT_SECONDS=60  NOTIV_LLM_MAX_TOKENS=16000  NOTIV_LLM_COOLDOWN_SECONDS=30
    NOTIV_LLM_HARD_COOLDOWN_SECONDS=600   (bad key / no credits / unsupported model: waiting a minute does not help)

Each provider lives in its own adapter module (llm_openai / llm_mistral / llm_anthropic) and imports its SDK lazily, so a
missing package only removes that provider. A provider that fails is put on a cool-down and the next one is tried;
LLMUnavailable is raised only when every provider failed, carrying one reason code per provider, which describe() turns
into an accurate message (no credits is not "busy"). Lecture text, prompts and keys are never logged or put in an error.
"""
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from . import obs

log = obs.get_logger("llm")

DEFAULT_ORDER = ("openai", "groq", "mistral", "anthropic")
DEFAULT_VISION_ORDER = ("openai", "groq", "anthropic")
KNOWN = ("openai", "groq", "mistral", "anthropic", "lovable")
# failures that a short wait cannot fix: keep the provider out of rotation for longer
HARD = {"auth", "quota_exhausted", "unsupported_model", "not_installed", "daily_limit"}
TRANSIENT = {"rate_limited", "unavailable", "timeout"}

REASON_TEXT = {
    "timeout": "timed out",
    "rate_limited": "is rate-limiting requests (try again shortly)",
    "daily_limit": "has used up this account's daily token/request limit for the model (it frees up over the next hours)",
    "quota_exhausted": "has no API credits / quota left on this account",
    "auth": "rejected the API key (invalid or lacks permission)",
    "unsupported_model": "does not serve the configured model",
    "unavailable": "is unavailable (server or network error)",
    "bad_request": "rejected the request",
    "refusal": "declined the request",
    "truncated": "returned a cut-off answer",
    "empty": "returned an empty answer",
    "not_installed": "SDK is not installed on this server",
    "error": "failed",
}


class LLMError(RuntimeError):
    """One provider failed. `reason` is one of REASON_TEXT's keys."""
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason, self.detail = reason, detail


class LLMUnavailable(RuntimeError):
    """No provider produced an answer. `failures` = [(provider, reason)], in the order tried. `retry_after` = the
    longest wait a rate-limiting provider asked for (seconds), when one said so."""
    def __init__(self, failures: List[tuple], retry_after: Optional[float] = None):
        self.failures, self.retry_after = failures, retry_after
        super().__init__("no answer model available" + (": " + ", ".join(f"{p}={r}" for p, r in failures) if failures else
                                                          " (none configured)"))

    @property
    def all_refused(self) -> bool:
        return bool(self.failures) and all(r == "refusal" for _, r in self.failures)

    @property
    def transient(self) -> bool:
        """At least one provider failed for a reason that waiting can fix (rate limit, outage, timeout). Providers that
        failed hard (no credits, bad key, wrong model) are on a long cool-down and skipped on the retry, so e.g. "OpenAI
        has no credits + Groq is rate-limited" is still worth waiting for Groq."""
        return any(r in TRANSIENT for _, r in self.failures)

    @property
    def only(self) -> Optional[str]:
        """The single reason shared by every provider, if there is one."""
        rs = {r for _, r in self.failures}
        return rs.pop() if len(rs) == 1 else None

    def describe(self) -> str:
        if not self.failures:
            return "No AI provider is configured on this server."
        return "; ".join(f"{p.capitalize()} {REASON_TEXT.get(r, 'failed')}" for p, r in self.failures) + "."


@dataclass
class Completion:
    text: str
    provider: str
    model: str


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def timeout_seconds() -> float:
    return _env_float("NOTIV_LLM_TIMEOUT_SECONDS", 60.0)


def max_tokens() -> int:
    return int(_env_float("NOTIV_LLM_MAX_TOKENS", 16000))


def _error_fields(e: Exception) -> tuple:
    """(code, type, message) from an SDK error body, e.g. OpenAI {"error": {"code": "insufficient_quota", ...}}."""
    body = getattr(e, "body", None)
    err = body.get("error", body) if isinstance(body, dict) else None
    if not isinstance(err, dict):
        return "", "", ""
    return str(err.get("code") or "").lower(), str(err.get("type") or "").lower(), str(err.get("message") or "").lower()


def retry_after(e: Exception) -> Optional[float]:
    """Seconds a provider asked us to wait (Retry-After header), if it said."""
    resp = getattr(e, "response", None)
    try:
        v = resp.headers.get("retry-after") if resp is not None else None
        return float(v) if v is not None else None
    except (TypeError, ValueError, AttributeError):
        return None


def classify(e: Exception) -> str:
    """Provider-neutral reason code for an SDK exception (each SDK has its own classes; status codes are shared)."""
    if isinstance(e, LLMError):
        return e.reason
    if isinstance(e, ImportError):
        return "not_installed"
    name = type(e).__name__.lower()
    if "timeout" in name:
        return "timeout"
    code, etype, msg = _error_fields(e)
    if "per day" in msg or "(tpd)" in msg or "(rpd)" in msg:
        return "daily_limit"                                # e.g. Groq free tier: 200k tokens/day; minutes of waiting won't do
    if "quota" in code or "quota" in etype or "credit" in code or "billing" in code or "billing" in etype:
        return "quota_exhausted"                            # OpenAI sends these as HTTP 429, but waiting will not help
    if "model_not_found" in code or "model_not_found" in etype or ("model" in code and "not" in code):
        return "unsupported_model"
    status = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
    if status in (401, 403):
        return "auth"
    if status == 402:
        return "quota_exhausted"
    if status == 404:
        return "unsupported_model"
    if status == 429:
        return "rate_limited"
    if isinstance(status, int) and status >= 500:
        return "unavailable"
    if status == 400 or status == 422:
        return "bad_request"
    if "connection" in name:
        return "unavailable"
    return "error"


class Provider:
    """Adapter contract. `complete` returns the model's text (JSON text when `schema` is given) or raises. Providers
    that can read images set `vision_model` and implement `see`."""
    name = "?"
    vision_model: Optional[str] = None

    def __init__(self, model: str):
        self.model = model

    def configured(self) -> bool:
        raise NotImplementedError

    def installed(self) -> bool:
        raise NotImplementedError

    def complete(self, system: str, user: str, schema: Optional[dict], schema_name: str) -> str:
        raise NotImplementedError

    def see(self, system: str, user: str, png: bytes) -> str:
        raise LLMError("bad_request", "this provider cannot read images")

    def model_available(self) -> Optional[bool]:
        """True/False when the endpoint can list its models; None when it cannot be checked."""
        return None


def _factories() -> Dict[str, Callable[[], Provider]]:
    from . import llm_anthropic, llm_mistral, llm_openai
    return {"openai": llm_openai.OpenAIProvider.from_env, "groq": llm_openai.GroqProvider.from_env,
            "lovable": llm_openai.LovableProvider.from_env, "mistral": llm_mistral.MistralProvider.from_env,
            "anthropic": llm_anthropic.AnthropicProvider.from_env}


def _order(env: str, default: tuple) -> List[str]:
    raw = os.getenv(env) or ",".join(default)               # blank in .env = the default order
    out = []
    for p in (x.strip().lower() for x in raw.split(",")):
        if p in KNOWN and p not in out:
            out.append(p)
    return out


def configured_order() -> List[str]:
    return _order("NOTIV_LLM_PROVIDERS", DEFAULT_ORDER)


def vision_order() -> List[str]:
    return _order("NOTIV_VISION_PROVIDERS", DEFAULT_VISION_ORDER)


class LLMChain:
    """Callable as `llm(system, user) -> str` (the contract AskService already uses); `complete()` adds JSON-schema
    output and reports which provider answered; `see()` reads an image (vision chains)."""

    def __init__(self, providers: List[Provider], cooldown: Optional[float] = None, clock: Callable[[], float] = time.monotonic):
        self.providers = providers
        self.cooldown = _env_float("NOTIV_LLM_COOLDOWN_SECONDS", 30.0) if cooldown is None else cooldown
        self.hard_cooldown = max(self.cooldown, _env_float("NOTIV_LLM_HARD_COOLDOWN_SECONDS", 600.0)) if cooldown is None else cooldown
        self.clock = clock
        self._down: Dict[str, float] = {}
        self._why: Dict[str, str] = {}
        self._lock = threading.Lock()

    def __call__(self, system: str, user: str) -> str:
        return self.complete(system, user).text

    def __bool__(self) -> bool:
        return bool(self.providers)

    def complete(self, system: str, user: str, schema: Optional[dict] = None, schema_name: str = "output") -> Completion:
        return self._run(lambda p: p.complete(system, user, schema, schema_name), lambda p: p.model)

    def see(self, system: str, user: str, png: bytes) -> Completion:
        return self._run(lambda p: p.see(system, user, png), lambda p: p.vision_model or p.model)

    def _run(self, call, model_of, providers: Optional[List[Provider]] = None) -> Completion:
        failures, cooling, wait = [], [], None
        now = self.clock()
        for p in (providers or self.providers):
            with self._lock:
                until, why = self._down.get(p.name, 0.0), self._why.get(p.name)
            if until > now and providers is None:            # an explicit retry list ignores the cool-down
                cooling.append((p, why))
                continue
            try:
                text = call(p)
                if not (text or "").strip():
                    raise LLMError("empty")
            except Exception as e:                           # noqa: BLE001 — any provider failure moves on to the next
                reason = classify(e)
                failures.append((p.name, reason))
                obs.log_event(log, "llm_provider_failed", logging.WARNING, provider=p.name, model=model_of(p), reason=reason,
                              error_class=type(e).__name__)
                obs.inc("notiv_llm_failures_total", help="answer-model calls that failed, by provider and reason",
                        provider=p.name, reason=reason)
                ra = retry_after(e) if reason == "rate_limited" else None
                if ra is not None:
                    wait = max(wait or 0.0, ra)
                if reason != "refusal":                      # a refusal is about this request, not the provider's health
                    with self._lock:
                        self._down[p.name] = self.clock() + (self.hard_cooldown if reason in HARD else
                                                             ra if ra is not None else self.cooldown)
                        self._why[p.name] = reason
                continue
            with self._lock:
                self._down.pop(p.name, None)
                self._why.pop(p.name, None)
            if failures:
                obs.log_event(log, "llm_fallback_used", provider=p.name, after=",".join(f[0] for f in failures))
            obs.inc("notiv_llm_calls_total", help="answer-model calls that succeeded, by provider", provider=p.name)
            return Completion(text, p.name, model_of(p))
        if cooling and providers is None:
            # a transient cool-down is worth a late retry; a hard one (no credits, bad key) is reported, not retried
            soft = [p for p, why in cooling if why not in HARD]
            if soft and not failures:
                return self._run(call, model_of, soft)
            failures += [(p.name, why or "unavailable") for p, why in cooling]
        raise LLMUnavailable(failures, wait)


def call_with_backoff(fn: Callable[[], "Completion"], max_wait: float, sleep: Callable[[float], None] = time.sleep,
                      clock: Callable[[], float] = time.monotonic):
    """For BATCH work (page transcription, study generation): when every provider failed transiently (rate limit,
    outage, timeout), wait — the provider's Retry-After when given, else exponential backoff — and try again, up to
    `max_wait` seconds in total. Hard failures (no credits, bad key, unsupported model) are raised immediately.
    Interactive requests (Ask, quiz) do not use this: they report the failure instead of making the user wait."""
    start, n = clock(), 0
    while True:
        try:
            return fn()
        except LLMUnavailable as e:
            if not e.transient:
                raise
            delay = e.retry_after if e.retry_after is not None else min(60.0, 5.0 * (2 ** n))
            if clock() - start + delay > max_wait:
                raise
            n += 1
            sleep(delay + 0.5)


def build_chain(order: Optional[List[str]] = None) -> LLMChain:
    """Providers in configured order that have credentials AND an installed SDK."""
    fac = _factories()
    return LLMChain([p for p in (fac[n]() for n in (order or configured_order())) if p.configured() and p.installed()])


def build_vision_chain() -> LLMChain:
    fac = _factories()
    return LLMChain([p for p in (fac[n]() for n in vision_order())
                     if p.configured() and p.installed() and p.vision_model])


def status() -> dict:
    """For /readyz and operators: names, models and state only, never keys."""
    fac = _factories()
    rows = []
    for name in configured_order():
        p = fac[name]()
        rows.append({"provider": name, "model": p.model, "vision_model": p.vision_model,
                     "configured": p.configured(), "installed": p.installed()})
    active = [r["provider"] for r in rows if r["configured"] and r["installed"]]
    vision = [p.name for p in build_vision_chain().providers]
    problems = [f"{r['provider']}: SDK not installed" for r in rows if r["configured"] and not r["installed"]]
    return {"order": [r["provider"] for r in rows], "active": active, "vision": vision, "providers": rows, "problems": problems}


def diagnose(live: bool = True) -> List[dict]:
    """Per configured provider: is the model served by its endpoint, and does one tiny real request succeed? Costs one
    very small request per provider when `live`. Returns names, models and reason codes only — never keys."""
    out = []
    fac = _factories()
    for name in configured_order():
        p = fac[name]()
        row = {"provider": name, "model": p.model, "configured": p.configured(), "installed": p.installed()}
        if not (row["configured"] and row["installed"]):
            out.append({**row, "ok": False, "reason": "not_configured" if not row["configured"] else "not_installed"})
            continue
        try:
            row["model_listed"] = p.model_available()
        except Exception as e:                               # noqa: BLE001
            row["model_listed"], row["list_error"] = None, classify(e)
        if row.get("model_listed") is False:
            out.append({**row, "ok": False, "reason": "unsupported_model"})
            continue
        if row.get("list_error") in HARD:
            out.append({**row, "ok": False, "reason": row["list_error"]})
            continue
        if live:
            t = time.monotonic()
            try:
                text = p.complete("Reply with the single word OK.", "Reply with OK.", None, "ping")
                row.update(ok=bool((text or "").strip()), reason=None if (text or "").strip() else "empty")
            except Exception as e:                           # noqa: BLE001
                row.update(ok=False, reason=classify(e))
            row["seconds"] = round(time.monotonic() - t, 2)
        out.append(row)
    return out
