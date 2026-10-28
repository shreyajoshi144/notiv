"""Anthropic adapter (Messages API, official `anthropic` SDK).

Claude Opus 5.5 runs adaptive thinking (it cannot be disabled); `effort` is the cost/quality control. Server-side refusal
fallback is on (`fallbacks="default"`, beta server-side-fallback-2026-07-01): a request a safety classifier declines is
re-run by Anthropic on its recommended fallback model inside the same call. Sampling parameters are not sent (rejected
by the API as of 2026-10-21)."""
import base64
import importlib.util
import os
from typing import Optional

from .llm import LLMError, Provider, max_tokens, timeout_seconds

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, model: str, effort: str = "low", server_fallback: bool = True):
        super().__init__(model)
        self.effort, self.server_fallback = effort, server_fallback
        self.vision_model = model
        self._client = None

    @classmethod
    def from_env(cls) -> "AnthropicProvider":
        return cls(os.getenv("NOTIV_ANTHROPIC_MODEL", "claude-opus-5-5"), os.getenv("NOTIV_ANTHROPIC_EFFORT", "low"),
                   os.getenv("NOTIV_ANTHROPIC_SERVER_FALLBACK", "1") != "0")

    def configured(self) -> bool:
        return bool(os.getenv("ANTHROPIC_API_KEY"))     # a server needs an explicit key; no CLI profiles

    def installed(self) -> bool:
        return importlib.util.find_spec("anthropic") is not None

    def client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), timeout=timeout_seconds(),
                                               max_retries=1)
        return self._client

    def complete(self, system: str, user: str, schema: Optional[dict], schema_name: str) -> str:
        output_config = {"effort": self.effort}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        return self._create(system, user, output_config)

    def see(self, system: str, user: str, png: bytes) -> str:
        image = {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": base64.standard_b64encode(png).decode("utf-8")}}
        return self._create(system, [image, {"type": "text", "text": user}], {"effort": self.effort})

    def _create(self, system: str, content, output_config: dict) -> str:
        kw = {"betas": [FALLBACK_BETA], "fallbacks": "default"} if self.server_fallback else {}
        r = self.client().beta.messages.create(model=self.model, max_tokens=max_tokens(), system=system,
                                               messages=[{"role": "user", "content": content}],
                                               output_config=output_config, **kw)
        if r.stop_reason == "refusal":                     # the whole server-side fallback chain declined
            raise LLMError("refusal", str(getattr(r.stop_details, "category", "") or "") if r.stop_details else "")
        if r.stop_reason == "max_tokens":
            raise LLMError("truncated")
        return "".join(b.text for b in r.content if b.type == "text")
