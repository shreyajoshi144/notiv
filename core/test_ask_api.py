"""Sprint 3 API-layer tests: Ask Notiv endpoints over real HTTP (server.dev_server mounts the same handlers,
auth and storage as the FastAPI routes), error contract, rate limiting, legacy /ask, static wiring."""
import ast
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from core.lecture import config
from core.lecture.align import Segment
from core.lecture.ask import NOT_ENOUGH
from core.lecture.make_test_lecture import make_lecture, make_text_pdf
from core.lecture.testkit import Env, segments
from core.test_api_boundary import FORBIDDEN_PREFIXES, _local_module_file, import_closure
from core.test_ask import SEGS
from server.api import ask_handlers as AH
from server.auth_tokens import create_token

try:
    import fastapi  # noqa: F401
    import httpx  # noqa: F401
    HAVE_FASTAPI = True
except ImportError:
    HAVE_FASTAPI = False

SECRET = "s" * 40
ALICE, BOB = 1, 2


class AskApiBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._old = {k: os.environ.get(k) for k in ("NOTIV_AUTH_SECRET", "NOTIV_ASK_LLM", "MISTRAL_API_KEY")}
        os.environ.update(NOTIV_AUTH_SECRET=SECRET, NOTIV_ASK_LLM="off")
        os.environ.pop("MISTRAL_API_KEY", None)
        cls.tmp = tempfile.mkdtemp()
        video, _, _ = make_lecture(os.path.join(cls.tmp, "fx"), [6, 9, 5, 7])
        pdf = make_text_pdf(os.path.join(cls.tmp, "fx"))
        cls.env = Env(cls.tmp)
        config.configure(cls.env.repo, cls.env.storage)
        cls.lid = cls.env.lecture(ALICE, video, pdf, title="API lecture")
        cls.env.process(cls.lid, segments(*SEGS))
        cls.rl = cls.env.lecture(ALICE, video, pdf, title="Rate-limit lecture")
        cls.env.process(cls.rl, segments(*SEGS[:3]))
        cls.queued = cls.env.lectures.create(ALICE, "queued")
        cls.env.jobs().enqueue(cls.queued)

    @classmethod
    def tearDownClass(cls):
        for k, v in cls._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(cls.tmp, ignore_errors=True)


class TestAskHandlers(AskApiBase):
    def test_error_contract(self):
        def status(fn, uid, lid, **kw):
            with self.assertRaises(AH.ApiError) as c:
                fn(uid, lid, **kw)
            return c.exception.status
        for fn, kw in ((AH.chat, {"question": "learning rate"}), (AH.chat_history, {}), (AH.chat_reset, {}), (AH.retrieve, {"q": "learning rate"})):
            self.assertEqual(status(fn, BOB, self.lid, **kw), 404, fn.__name__)
            self.assertEqual(status(fn, ALICE, 10 ** 6, **kw), 404, fn.__name__)
        self.assertEqual(status(AH.chat, ALICE, self.queued, question="learning rate"), 409)
        self.assertEqual(status(AH.chat, ALICE, self.lid, question=""), 400)
        self.assertEqual(status(AH.retrieve, ALICE, self.lid, q=""), 400)


class TestAskHttp(AskApiBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from server.dev_server import make_server
        cls.srv = make_server(0)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.tok = {u: create_token(u, SECRET, 3600) for u in (ALICE, BOB)}

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        super().tearDownClass()

    def call(self, method, path, body=None, user=ALICE):
        h = {"Content-Type": "application/json"}
        if user:
            h["Authorization"] = "Bearer " + self.tok[user]
        data = json.dumps(body).encode() if body is not None else None
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + path, data=data, headers=h, method=method)) as r:
                return r.status, json.loads(r.read()), r.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null"), e.headers

    def test_auth_required_everywhere(self):
        for method, path, body in (("POST", f"/lectures/{self.lid}/chat", {"question": "learning rate"}), ("GET", f"/lectures/{self.lid}/chat", None),
                                   ("DELETE", f"/lectures/{self.lid}/chat", None), ("GET", f"/lectures/{self.lid}/retrieve?q=learning", None),
                                   ("POST", f"/lectures/{self.lid}/ask", {"question": "learning rate"})):
            code, _, h = self.call(method, path, body, user=None)
            self.assertEqual((code, h["WWW-Authenticate"]), (401, "Bearer"), path)

    def test_non_members_cannot_read_write_or_clear(self):
        self.call("POST", f"/lectures/{self.lid}/chat", {"question": "What is the learning rate?"})
        for method, path, body in (("POST", "chat", {"question": "learning rate"}), ("GET", "chat", None), ("DELETE", "chat", None),
                                   ("GET", "retrieve?q=learning", None), ("POST", "ask", {"question": "learning rate"})):
            c1, b1, _ = self.call(method, f"/lectures/{self.lid}/{path}", body, user=BOB)
            c2, b2, _ = self.call(method, f"/lectures/999999/{path}", body, user=BOB)
            self.assertEqual((c1, b1), (c2, b2), path)
            self.assertEqual(c1, 404)
        code, h, _ = self.call("GET", f"/lectures/{self.lid}/chat")                      # Alice's chat untouched by Bob's DELETE attempt
        self.assertEqual(code, 200)
        self.assertGreaterEqual(len(h["messages"]), 2)

    def test_chat_conversation_lifecycle_over_http(self):
        self.call("DELETE", f"/lectures/{self.lid}/chat")
        code, m, _ = self.call("POST", f"/lectures/{self.lid}/chat", {"question": "What is the learning rate?"})
        self.assertEqual((code, m["grounded"], m["role"], m["origin_label"]), (200, True, "assistant", "Notiv answer"))
        self.assertEqual(m["citations"][0]["citation"]["label"], "Slide 1 · 00:03")
        code, m2, _ = self.call("POST", f"/lectures/{self.lid}/chat", {"question": "What about its step size?"})
        self.assertTrue(m2["grounded"])
        code, h, _ = self.call("GET", f"/lectures/{self.lid}/chat")
        self.assertEqual([x["role"] for x in h["messages"]], ["user", "assistant", "user", "assistant"])
        self.assertEqual(h["limits"]["max_question_chars"], 500)
        code, d, _ = self.call("DELETE", f"/lectures/{self.lid}/chat")
        self.assertEqual((code, d["deleted_messages"]), (200, 4))
        self.assertEqual(self.call("GET", f"/lectures/{self.lid}/chat")[1]["messages"], [])

    def test_insufficient_evidence_over_http(self):
        code, m, _ = self.call("POST", f"/lectures/{self.lid}/chat", {"question": "Who won the 1998 world cup?"})
        self.assertEqual((code, m["content"], m["grounded"], m["citations"]), (200, NOT_ENOUGH, False, []))

    def test_bad_requests(self):
        self.assertEqual(self.call("POST", f"/lectures/{self.lid}/chat", {"question": ""})[0], 422)
        self.assertEqual(self.call("POST", f"/lectures/{self.lid}/chat", {"question": "x" * 501})[0], 422)
        self.assertEqual(self.call("POST", f"/lectures/{self.lid}/chat", {"q": "typo"})[0], 422)
        self.assertEqual(self.call("POST", f"/lectures/{self.lid}/chat", {"question": 5})[0], 422)
        self.assertEqual(self.call("GET", f"/lectures/{self.lid}/retrieve")[0] in (400, 422), True)
        self.assertEqual(self.call("POST", f"/lectures/{self.queued}/chat", {"question": "learning rate"})[0], 409)

    def test_rate_limit_returns_429_with_retry_after(self):
        codes = [self.call("POST", f"/lectures/{self.rl}/chat", {"question": "Who won the 1998 world cup?"}) for _ in range(12)]
        self.assertEqual([c[0] for c in codes], [200] * 10 + [429, 429])
        self.assertEqual(codes[10][2]["Retry-After"], "60")
        self.assertEqual(codes[10][1]["detail"]["retry_after"], 60)

    def test_retrieve_endpoint(self):
        code, r, _ = self.call("GET", f"/lectures/{self.lid}/retrieve?q=How+does+backpropagation+work%3F&limit=3")
        self.assertEqual((code, r["sufficient"], r["mode"]), (200, True, "hybrid"))
        self.assertTrue(r["results"][0]["retrieval"]["rrf"] > 0)
        self.assertTrue(all("citation" in x and x["citation"]["label"] for x in r["results"]))
        code, r, _ = self.call("GET", f"/lectures/{self.lid}/retrieve?q=quantum+computing")   # closest candidates, but flagged insufficient
        self.assertEqual((code, r["sufficient"], r["reason"]), (200, False, "weak_match"))
        code, r, _ = self.call("GET", f"/lectures/{self.lid}/retrieve?q=1998+world+cup")
        self.assertEqual((r["sufficient"], r["reason"], r["results"]), (False, "no_match", []))

    def test_legacy_ask_route_keeps_its_contract_but_uses_the_hardened_pipeline(self):
        code, r, _ = self.call("POST", f"/lectures/{self.lid}/ask", {"question": "What is the learning rate?", "history": [{"question": "q", "answer": "a"}]})
        self.assertEqual(code, 200)
        self.assertTrue({"answer", "grounded", "citations", "follow_ups", "dropped_citations"} <= set(r))
        self.assertTrue(r["grounded"])
        self.assertEqual(set(r["citations"][0]), {"evidence_id", "lecture_id", "kind", "slide_index", "start", "end", "text"})
        code, r, _ = self.call("POST", f"/lectures/{self.lid}/ask", {"question": "Who won the 1998 world cup?"})
        self.assertEqual((r["answer"], r["grounded"]), (NOT_ENOUGH, False))

    def test_ui_has_an_ask_tab_that_renders_only_as_text(self):
        with urllib.request.urlopen(self.base + "/app/lectures/1") as r:
            page = r.read().decode()
        self.assertIn("/static/ui/lecture.js", page)
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", "server", "static", "ui")
        viewer, tools = (open(os.path.join(root, n), encoding="utf-8").read() for n in ("lecture.js", "lecture-tools.js"))
        self.assertIn('["ask", "Ask"]', viewer)
        for src in (viewer, tools):
            self.assertNotIn("innerHTML", src)
            self.assertNotIn("insertAdjacentHTML", src)
        self.assertIn("Clear this conversation?", tools)


class TestAskStaticWiring(unittest.TestCase):
    def test_main_mounts_ask_router(self):
        src = open(_local_module_file("server.main"), encoding="utf-8").read()
        self.assertIn("app.include_router(routes_ask.router)", src)
        self.assertIn("app.include_router(routes_lectures.router)", src)

    def test_legacy_ask_route_delegates_to_the_new_pipeline(self):
        src = open(_local_module_file("server.api.routes_lectures"), encoding="utf-8").read()
        self.assertIn("ask_handlers.ask_stateless", src)
        self.assertNotIn("qa.ask(", src)

    def test_all_ask_routes_exist_and_require_auth(self):
        tree = ast.parse(open(_local_module_file("server.api.routes_ask"), encoding="utf-8").read())
        fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.decorator_list]
        got = sorted(f"{d.func.attr} {d.args[0].value}" for d in (f.decorator_list[0] for f in fns))
        self.assertEqual(got, sorted(["post /lectures/{lecture_id}/chat", "get /lectures/{lecture_id}/chat",
                                      "delete /lectures/{lecture_id}/chat", "get /lectures/{lecture_id}/retrieve",
                                      "post /lectures/{lecture_id}/chat/{message_id}/general-knowledge"]))
        for fn in fns:
            self.assertIn("get_current_user", ast.unparse(fn.args), fn.name)

    def test_ask_api_does_not_reach_the_legacy_stack(self):
        for start in ("server.api.routes_ask", "server.api.ask_handlers", "server.main"):
            bad = {m: by for m, by in import_closure(start).items() if m.startswith(FORBIDDEN_PREFIXES)}
            self.assertEqual(bad, {}, f"{start} reaches legacy modules: {bad}")

    def test_no_prompt_or_secret_text_in_api_modules(self):
        for mod in ("server.api.routes_ask", "server.api.ask_handlers"):
            src = open(_local_module_file(mod), encoding="utf-8").read()
            self.assertNotIn("SYSTEM", src)
            self.assertNotIn("CANARY", src)


@unittest.skipUnless(HAVE_FASTAPI, "fastapi/httpx not installed")
class TestFastApiAskRoutes(AskApiBase):
    """Runs only where FastAPI is installed (NOT run in the authoring sandbox)."""

    def test_routes_registered_validated_and_authorized(self):
        from fastapi.testclient import TestClient
        from server.main import app
        c = TestClient(app)
        from core.route_table import route_table
        paths = route_table(app)
        for p in (("/lectures/{lecture_id}/chat", "POST"), ("/lectures/{lecture_id}/chat", "GET"),
                  ("/lectures/{lecture_id}/chat", "DELETE"), ("/lectures/{lecture_id}/retrieve", "GET")):
            self.assertIn(p, paths)
        self.assertEqual(c.post(f"/lectures/{self.lid}/chat", json={"question": "learning rate"}).status_code, 401)
        h = {"Authorization": "Bearer " + create_token(ALICE, SECRET, 3600)}
        self.assertEqual(c.post(f"/lectures/{self.lid}/chat", json={"question": ""}, headers=h).status_code, 422)
        self.assertEqual(c.post(f"/lectures/{self.lid}/chat", json={"question": "What is the learning rate?"}, headers=h).json()["grounded"], True)
        hb = {"Authorization": "Bearer " + create_token(BOB, SECRET, 3600)}
        self.assertEqual(c.get(f"/lectures/{self.lid}/chat", headers=hb).status_code, 404)


if __name__ == "__main__":
    unittest.main()
