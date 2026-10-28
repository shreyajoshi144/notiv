"""Answer-model layer (core.lecture.llm): ordered provider fallback, cool-downs, error classification, each adapter's
request shape (with fake SDK clients — no network), config wiring, and Ask Notiv running on the chain."""
import json
import os
import unittest
from types import SimpleNamespace as NS
from unittest import mock

from core.lecture import config, llm, llm_anthropic, llm_mistral, llm_openai
from core.test_ask import ALICE, AskBase

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["x"], "properties": {"x": {"type": "string"}}}
KEYS = ("OPENAI_API_KEY", "MISTRAL_API_KEY", "ANTHROPIC_API_KEY", "LOVABLE_API_KEY", "GROQ_API_KEY", "NOTIV_LLM_PROVIDERS",
        "NOTIV_ASK_LLM", "OPENAI_MODEL", "NOTIV_OPENAI_MODEL", "OPENAI_BASE_URL", "NOTIV_OPENAI_BASE_URL", "NOTIV_VISION_PROVIDERS")


class Fake(llm.Provider):
    def __init__(self, name, reply=None, error=None):
        super().__init__(name + "-model")
        self.name, self.reply, self.error, self.calls = name, reply, error, []

    def configured(self): return True
    def installed(self): return True

    def complete(self, system, user, schema, schema_name):
        self.calls.append((system, user, schema, schema_name))
        if self.error is not None:
            raise self.error
        return self.reply(system, user) if callable(self.reply) else self.reply


class StatusErr(Exception):
    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.status_code = code


class APITimeoutError(Exception):
    pass


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


class TestChain(unittest.TestCase):
    def test_first_healthy_provider_answers_and_reports_itself(self):
        a, b = Fake("openai", "A"), Fake("mistral", "B")
        done = llm.LLMChain([a, b], cooldown=30).complete("sys", "usr", SCHEMA, "quiz")
        self.assertEqual((done.text, done.provider, done.model), ("A", "openai", "openai-model"))
        self.assertEqual(b.calls, [])
        self.assertEqual(a.calls[0][2:], (SCHEMA, "quiz"))

    def test_failure_falls_through_in_order_and_cools_the_failed_provider(self):
        clock = Clock()
        a, b, c = Fake("openai", error=APITimeoutError()), Fake("mistral", error=StatusErr(503)), Fake("anthropic", "C")
        chain = llm.LLMChain([a, b, c], cooldown=30, clock=clock)
        self.assertEqual(chain("s", "u"), "C")
        self.assertEqual(chain("s", "u"), "C")
        self.assertEqual((len(a.calls), len(b.calls)), (1, 1))                    # cooling down: skipped on the 2nd call
        clock.t += 31
        chain("s", "u")
        self.assertEqual((len(a.calls), len(b.calls)), (2, 2))                    # retried once the cool-down passed

    def test_recovered_provider_is_used_again(self):
        clock, a = Clock(), Fake("openai", error=StatusErr(500))
        chain = llm.LLMChain([a, Fake("mistral", "B")], cooldown=30, clock=clock)
        chain("s", "u")
        clock.t += 31
        a.error, a.reply = None, "A"
        self.assertEqual(chain.complete("s", "u").provider, "openai")

    def test_refusal_moves_on_but_does_not_cool_the_provider(self):
        a = Fake("anthropic", error=llm.LLMError("refusal"))
        chain = llm.LLMChain([a, Fake("openai", "ok")], cooldown=30, clock=Clock())
        chain("s", "u"); chain("s", "u")
        self.assertEqual(len(a.calls), 2)

    def test_empty_text_counts_as_a_failure(self):
        self.assertEqual(llm.LLMChain([Fake("openai", "  "), Fake("mistral", "B")], cooldown=0).complete("s", "u").provider, "mistral")

    def test_all_failing_raises_with_one_reason_per_provider(self):
        chain = llm.LLMChain([Fake("openai", error=StatusErr(429)), Fake("mistral", error=StatusErr(401)),
                              Fake("anthropic", error=llm.LLMError("refusal"))], cooldown=30)
        with self.assertRaises(llm.LLMUnavailable) as cm:
            chain("s", "u")
        self.assertEqual(cm.exception.failures, [("openai", "rate_limited"), ("mistral", "auth"), ("anthropic", "refusal")])
        self.assertFalse(cm.exception.all_refused)

    def test_all_refused(self):
        with self.assertRaises(llm.LLMUnavailable) as cm:
            llm.LLMChain([Fake("openai", error=llm.LLMError("refusal"))]).complete("s", "u")
        self.assertTrue(cm.exception.all_refused)

    def test_when_everything_is_cooling_down_it_still_tries(self):
        clock, a = Clock(), Fake("openai", error=StatusErr(500))
        chain = llm.LLMChain([a], cooldown=30, clock=clock)
        with self.assertRaises(llm.LLMUnavailable):
            chain("s", "u")
        a.error, a.reply = None, "back"
        self.assertEqual(chain("s", "u"), "back")

    def test_empty_chain_is_falsy_and_unavailable(self):
        chain = llm.LLMChain([])
        self.assertFalse(chain)
        with self.assertRaises(llm.LLMUnavailable):
            chain("s", "u")

    def test_errors_never_carry_prompt_or_key(self):
        chain = llm.LLMChain([Fake("openai", error=StatusErr(500))])
        with self.assertRaises(llm.LLMUnavailable) as cm:
            chain("SECRET SYSTEM PROMPT", "lecture text")
        self.assertNotIn("SECRET", str(cm.exception))

    def test_classify(self):
        for e, want in [(APITimeoutError(), "timeout"), (StatusErr(401), "auth"), (StatusErr(403), "auth"),
                        (StatusErr(429), "rate_limited"), (StatusErr(500), "unavailable"), (StatusErr(529), "unavailable"),
                        (StatusErr(400), "bad_request"), (ImportError("x"), "not_installed"), (ValueError("x"), "error"),
                        (llm.LLMError("truncated"), "truncated")]:
            self.assertEqual(llm.classify(e), want, e)


class TestBuildAndConfig(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {k: "" for k in KEYS})
        p.start(); self.addCleanup(p.stop)
        config._chain = config._chain_key = None
        self.addCleanup(lambda: setattr(config, "_chain", None))

    def test_default_order_and_only_keyed_providers(self):
        os.environ.update(ANTHROPIC_API_KEY="a", OPENAI_API_KEY="o")
        self.assertEqual([p.name for p in llm.build_chain().providers], ["openai", "anthropic"])

    def test_custom_order_unknown_and_duplicate_names_ignored(self):
        os.environ.update(NOTIV_LLM_PROVIDERS="anthropic, bogus ,openai,anthropic,lovable", ANTHROPIC_API_KEY="a",
                          OPENAI_API_KEY="o", LOVABLE_API_KEY="l")
        self.assertEqual(llm.configured_order(), ["anthropic", "openai", "lovable"])
        self.assertEqual([p.name for p in llm.build_chain().providers], ["anthropic", "openai", "lovable"])

    def test_lovable_is_opt_in(self):
        os.environ.update(LOVABLE_API_KEY="l")
        self.assertEqual(llm.build_chain().providers, [])

    def test_provider_without_its_sdk_is_skipped_and_reported(self):
        os.environ.update(OPENAI_API_KEY="o", MISTRAL_API_KEY="m")
        with mock.patch.object(llm_openai.OpenAIProvider, "installed", lambda self: False):
            self.assertEqual([p.name for p in llm.build_chain().providers], ["mistral"])
            st = llm.status()
        self.assertEqual(st["active"], ["mistral"])
        self.assertIn("openai: SDK not installed", st["problems"])
        self.assertNotIn("o", [r.get("key") for r in st["providers"]])
        self.assertTrue(all("key" not in r for r in st["providers"]))

    def test_get_llm(self):
        self.assertIsNone(config.get_llm())                                       # no keys -> quote-only Ask
        os.environ["MISTRAL_API_KEY"] = "m"
        self.assertEqual([p.name for p in config.get_llm().providers], ["mistral"])
        os.environ["NOTIV_ASK_LLM"] = "off"
        self.assertIsNone(config.get_llm())

    def test_chain_is_shared_and_rebuilt_when_keys_change(self):
        os.environ["OPENAI_API_KEY"] = "o"
        c1 = config.get_llm_chain()
        self.assertIs(config.get_llm_chain(), c1)                                 # cool-downs persist across requests
        os.environ["ANTHROPIC_API_KEY"] = "a"
        self.assertEqual([p.name for p in config.get_llm_chain().providers], ["openai", "anthropic"])


# ── adapters, against fake SDK clients ───────────────────────────────────────
class TestOpenAIAdapter(unittest.TestCase):
    def provider(self, response):
        p = llm_openai.OpenAIProvider("gpt-6-astra", "low")
        p.calls = []
        p._client = NS(responses=NS(create=lambda **kw: p.calls.append(kw) or response))
        return p

    def test_request_shape(self):
        p = self.provider(NS(output=[], status="completed", output_text='{"x":"y"}'))
        self.assertEqual(p.complete("SYS", "USER", SCHEMA, "quiz"), '{"x":"y"}')
        kw = p.calls[0]
        self.assertEqual((kw["model"], kw["instructions"], kw["input"], kw["store"]), ("gpt-6-astra", "SYS", "USER", False))
        self.assertEqual(kw["reasoning"], {"effort": "low"})
        self.assertEqual(kw["text"], {"format": {"type": "json_schema", "name": "quiz", "schema": SCHEMA, "strict": True}})
        self.assertNotIn("temperature", kw)

    def test_plain_text_has_no_format(self):
        p = self.provider(NS(output=[], status="completed", output_text="hi"))
        p.complete("s", "u", None, "x")
        self.assertNotIn("text", p.calls[0])

    def test_refusal_and_truncation(self):
        refused = NS(output=[NS(type="message", content=[NS(type="refusal", refusal="no")])], status="completed", output_text="")
        with self.assertRaises(llm.LLMError) as cm:
            self.provider(refused).complete("s", "u", None, "x")
        self.assertEqual(cm.exception.reason, "refusal")
        cut = NS(output=[], status="incomplete", incomplete_details=NS(reason="max_output_tokens"), output_text="{")
        with self.assertRaises(llm.LLMError) as cm:
            self.provider(cut).complete("s", "u", None, "x")
        self.assertEqual(cm.exception.reason, "truncated")

    def test_lovable_uses_the_gateway_with_its_own_key(self):
        with mock.patch.dict(os.environ, {"LOVABLE_API_KEY": "l", "OPENAI_API_KEY": ""}):
            p = llm_openai.LovableProvider.from_env()
            self.assertTrue(p.configured())
            self.assertEqual((p.base_url, p.model), ("https://ai.gateway.lovable.dev/v1", "openai/gpt-6-astra"))
            self.assertEqual(str(p.client().base_url).rstrip("/"), "https://ai.gateway.lovable.dev/v1")


class TestAnthropicAdapter(unittest.TestCase):
    def provider(self, response, **kw):
        p = llm_anthropic.AnthropicProvider("claude-opus-5-5", "low", **kw)
        p.calls = []
        p._client = NS(beta=NS(messages=NS(create=lambda **k: p.calls.append(k) or response)))
        return p

    def msg(self, stop="end_turn", blocks=None, details=None):
        return NS(stop_reason=stop, stop_details=details,
                  content=blocks if blocks is not None else [NS(type="thinking", thinking=""), NS(type="text", text='{"x":"y"}')])

    def test_request_shape_with_server_side_refusal_fallback(self):
        p = self.provider(self.msg())
        self.assertEqual(p.complete("SYS", "USER", SCHEMA, "quiz"), '{"x":"y"}')            # thinking blocks skipped
        kw = p.calls[0]
        self.assertEqual((kw["model"], kw["system"], kw["messages"]), ("claude-opus-5-5", "SYS", [{"role": "user", "content": "USER"}]))
        self.assertEqual(kw["output_config"], {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}})
        self.assertEqual((kw["betas"], kw["fallbacks"]), (["server-side-fallback-2026-07-01"], "default"))
        for banned in ("temperature", "top_p", "top_k", "thinking"):
            self.assertNotIn(banned, kw)

    def test_server_fallback_can_be_switched_off(self):
        p = self.provider(self.msg(), server_fallback=False)
        p.complete("s", "u", None, "x")
        self.assertNotIn("fallbacks", p.calls[0])
        self.assertEqual(p.calls[0]["output_config"], {"effort": "low"})

    def test_refusal_and_truncation(self):
        with self.assertRaises(llm.LLMError) as cm:
            self.provider(self.msg("refusal", [], NS(category="cyber"))).complete("s", "u", None, "x")
        self.assertEqual(cm.exception.reason, "refusal")
        with self.assertRaises(llm.LLMError) as cm:
            self.provider(self.msg("max_tokens")).complete("s", "u", None, "x")
        self.assertEqual(cm.exception.reason, "truncated")


class TestMistralAdapter(unittest.TestCase):
    def provider(self, content, finish="stop"):
        p = llm_mistral.MistralProvider("mistral-large-latest")
        p.calls = []
        r = NS(choices=[NS(finish_reason=finish, message=NS(content=content))])
        p._client = NS(chat=NS(complete=lambda **k: p.calls.append(k) or r))
        return p

    def test_request_shape(self):
        p = self.provider('{"x":"y"}')
        self.assertEqual(p.complete("SYS", "USER", SCHEMA, "quiz"), '{"x":"y"}')
        kw = p.calls[0]
        self.assertEqual(kw["messages"], [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}])
        rf = kw["response_format"]
        self.assertEqual((rf.type, rf.json_schema.name, rf.json_schema.schema_definition, rf.json_schema.strict),
                         ("json_schema", "quiz", SCHEMA, True))
        self.assertEqual(rf.model_dump(by_alias=True)["json_schema"]["schema"], SCHEMA)       # sent as "schema" on the wire

    def test_chunked_content_and_truncation(self):
        self.assertEqual(self.provider([NS(type="text", text="a"), NS(type="text", text="b")]).complete("s", "u", None, "x"), "ab")
        with self.assertRaises(llm.LLMError) as cm:
            self.provider("{", finish="length").complete("s", "u", None, "x")
        self.assertEqual(cm.exception.reason, "truncated")


# ── Ask Notiv on the chain (real retrieval, citation checks and persistence) ─────
class TestAskOnTheChain(AskBase):
    def test_fallback_provider_writes_the_cited_answer(self):
        a = self.ev_id(self.lid, "The learning rate")
        good = Fake("mistral", lambda s, u: f"The learning rate controls how big a step is taken downhill on each update [E{a}].")
        chain = llm.LLMChain([Fake("openai", error=StatusErr(503)), good], cooldown=30)
        self.reset()
        m = self.svc(chain).chat(ALICE, self.lid, "What is the learning rate?")
        self.assertEqual((m["answer_mode"], m["grounded"]), ("llm", True))
        self.assertEqual([c["evidence_id"] for c in m["citations"]], [a])
        self.assertEqual(len(good.calls), 1)

    def test_every_provider_down_degrades_to_quotes(self):
        chain = llm.LLMChain([Fake("openai", error=StatusErr(500)), Fake("anthropic", error=APITimeoutError())], cooldown=30)
        self.reset()
        m = self.svc(chain).chat(ALICE, self.lid, "What is the learning rate?")
        self.assertEqual((m["grounded"], m["answer_mode"], m["degraded"]), (True, "extractive", True))
        self.assertIn("The learning rate controls how big a step", m["content"])
        self.assertNotIn("HTTP 500", json.dumps(m))


if __name__ == "__main__":
    unittest.main()
