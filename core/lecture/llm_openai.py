"""OpenAI adapter (Responses API, official `openai` SDK), plus the OpenAI-compatible endpoints that use the same SDK:
Groq and the Lovable AI Gateway (same adapter, different base URL and key)."""
import base64
import importlib.util
import os
import threading
from typing import Optional

from .llm import LLMError, Provider, max_tokens, timeout_seconds


def _env(*names: str, default: str = "") -> str:
    for n in names:
        v = (os.getenv(n) or "").strip()
        if v:
            return v
    return default


class OpenAIProvider(Provider):
    name = "openai"
    key_env = "OPENAI_API_KEY"
    base_url: Optional[str] = None
    default_headers: Optional[dict] = None

    def __init__(self, model: str, effort: str = "low", vision_model: Optional[str] = None, base_url: Optional[str] = None):
        super().__init__(model)
        self.effort, self.vision_model = effort, vision_model
        if base_url:
            self.base_url = base_url
        self._client = None
        self._listed: Optional[set] = None
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "OpenAIProvider":
        # NOTIV_* wins; the SDK-standard OPENAI_MODEL / OPENAI_BASE_URL are honoured too (they were silently ignored before)
        model = _env("NOTIV_OPENAI_MODEL", "OPENAI_MODEL", default="gpt-6-astra")
        return cls(model, _env("NOTIV_OPENAI_EFFORT", default="low"), _env("NOTIV_OPENAI_VISION_MODEL", default=model),
                   _env("NOTIV_OPENAI_BASE_URL", "OPENAI_BASE_URL") or None)

    def configured(self) -> bool:
        return bool(os.getenv(self.key_env))

    def installed(self) -> bool:
        return importlib.util.find_spec("openai") is not None

    def client(self):
        if self._client is None:
            import openai
            # one SDK retry, then the chain moves on to the next provider instead of waiting out long backoffs
            self._client = openai.OpenAI(api_key=os.getenv(self.key_env), base_url=self.base_url,
                                         default_headers=self.default_headers, timeout=timeout_seconds(), max_retries=1)
        return self._client

    def model_available(self, model: Optional[str] = None) -> Optional[bool]:
        """Is `model` served by this endpoint? Listed once per process (models.list is free)."""
        with self._lock:
            if self._listed is None:
                self._listed = {m.id for m in self.client().models.list()}
        return (model or self.model) in self._listed

    def _check_model(self, model: str) -> None:
        try:
            ok = self.model_available(model)
        except Exception:                                    # noqa: BLE001 — the real request below reports the failure
            return
        if ok is False:
            raise LLMError("unsupported_model", f"{model} is not served by {self.base_url or 'api.openai.com'}")

    def complete(self, system: str, user: str, schema: Optional[dict], schema_name: str) -> str:
        self._check_model(self.model)
        kw = {}
        if schema is not None:
            kw["text"] = {"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}}
        # reasoning models reject temperature/top_p; reasoning effort is the quality/cost control
        r = self.client().responses.create(model=self.model, instructions=system, input=user, store=False,
                                           reasoning={"effort": self.effort}, max_output_tokens=max_tokens(), **kw)
        return self._text(r)

    @staticmethod
    def _text(r) -> str:
        for item in getattr(r, "output", None) or []:
            for part in getattr(item, "content", None) or []:
                if getattr(part, "type", None) == "refusal":
                    raise LLMError("refusal")
        if getattr(r, "status", None) == "incomplete":
            raise LLMError("truncated", str(getattr(getattr(r, "incomplete_details", None), "reason", "") or ""))
        return r.output_text or ""

    def see(self, system: str, user: str, png: bytes) -> str:
        model = self.vision_model or self.model
        self._check_model(model)
        url = "data:image/png;base64," + base64.b64encode(png).decode()
        r = self.client().responses.create(model=model, instructions=system, store=False, max_output_tokens=max_tokens(),
                                           input=[{"role": "user", "content": [{"type": "input_text", "text": user},
                                                                                {"type": "input_image", "image_url": url}]}])
        return self._text(r)


class GroqProvider(OpenAIProvider):
    """Groq (OpenAI-compatible). Text: Responses API with strict JSON schema (gpt-oss models). Images: Chat Completions with
    an image-capable model (qwen/qwen3.8-27b), the only shape Groq accepts for image input."""
    name = "groq"
    key_env = "GROQ_API_KEY"
    base_url = "https://api.groq.com/openai/v1"

    @classmethod
    def from_env(cls) -> "GroqProvider":
        return cls(_env("NOTIV_GROQ_MODEL", default="openai/gpt-oss-120b"), _env("NOTIV_GROQ_EFFORT", default="low"),
                   _env("NOTIV_GROQ_VISION_MODEL", default="qwen/qwen3.8-27b"))

    def see(self, system: str, user: str, png: bytes) -> str:
        model = self.vision_model or self.model
        self._check_model(model)
        url = "data:image/png;base64," + base64.b64encode(png).decode()
        r = self.client().chat.completions.create(
            model=model, max_tokens=min(max_tokens(), 8000),
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": [{"type": "text", "text": user}, {"type": "image_url", "image_url": {"url": url}}]}])
        choice = (r.choices or [None])[0]
        if choice is None:
            raise LLMError("empty")
        if choice.finish_reason == "length":
            raise LLMError("truncated")
        return choice.message.content or ""


class LovableProvider(OpenAIProvider):
    """Lovable AI Gateway (OpenAI-compatible; billed in Lovable credits). Opt-in: list "lovable" in NOTIV_LLM_PROVIDERS."""
    name = "lovable"
    key_env = "LOVABLE_API_KEY"
    base_url = "https://ai.gateway.lovable.dev/v1"
    default_headers = {"X-Lovable-AIG-SDK": "openai"}

    @classmethod
    def from_env(cls) -> "LovableProvider":
        return cls(_env("NOTIV_LOVABLE_MODEL", default="openai/gpt-6-astra"), _env("NOTIV_OPENAI_EFFORT", default="low"))

    def model_available(self, model: Optional[str] = None) -> Optional[bool]:
        return None                                          # the gateway's model list is not verified; let the request decide
