"""Mistral adapter (chat completions, official `mistralai` SDK)."""
import importlib.util
import os
from typing import Optional

from .llm import LLMError, Provider, max_tokens, timeout_seconds


class MistralProvider(Provider):
    name = "mistral"

    def __init__(self, model: str):
        super().__init__(model)
        self._client = None

    @classmethod
    def from_env(cls) -> "MistralProvider":
        return cls(os.getenv("NOTIV_MISTRAL_MODEL", "mistral-large-latest"))

    def configured(self) -> bool:
        return bool(os.getenv("MISTRAL_API_KEY"))

    def installed(self) -> bool:
        return importlib.util.find_spec("mistralai") is not None

    def client(self):
        if self._client is None:
            from mistralai.client import Mistral
            self._client = Mistral(api_key=os.getenv("MISTRAL_API_KEY"), timeout_ms=int(timeout_seconds() * 1000))
        return self._client

    def complete(self, system: str, user: str, schema: Optional[dict], schema_name: str) -> str:
        kw = {}
        if schema is not None:
            from mistralai.client.models import JSONSchema, ResponseFormat
            kw["response_format"] = ResponseFormat(type="json_schema", json_schema=JSONSchema(
                name=schema_name, schema_definition=schema, strict=True))
        r = self.client().chat.complete(model=self.model, max_tokens=max_tokens(), temperature=0.2,
                                        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                                        **kw)
        choice = (r.choices or [None])[0]
        if choice is None:
            raise LLMError("empty")
        if getattr(choice, "finish_reason", None) == "length":
            raise LLMError("truncated")
        content = choice.message.content
        if isinstance(content, list):                        # content chunks: keep the text ones
            content = "".join(getattr(c, "text", "") or "" for c in content)
        return content or ""
