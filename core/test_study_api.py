"""Sprint 4 API tests over real HTTP (server.dev_server mounts the same handlers/auth/storage as the FastAPI
routes): auth, error contract, the full study lifecycle, course memory, isolation, static wiring."""
import ast
import json
import os
import threading
import unittest
import urllib.error
import urllib.request

from core.test_api_boundary import FORBIDDEN_PREFIXES, _local_module_file, import_closure
from core.study_world import LAZY_WORLD
from server.auth_tokens import create_token

try:
    import fastapi  # noqa: F401
    import httpx  # noqa: F401
    HAVE_FASTAPI = True
except ImportError:
    HAVE_FASTAPI = False

W = LAZY_WORLD
SECRET = "s" * 40
from core.study_world import ALICE, BOB, EVE  # noqa: E402

EXPECTED_ROUTES = sorted([
    "get /lectures/{lecture_id}/knowledge", "get /lectures/{lecture_id}/concepts", "post /lectures/{lecture_id}/knowledge/regenerate",
    "delete /knowledge/{knowledge_id}", "post /knowledge/{knowledge_id}/review", "get /lectures/{lecture_id}/study/items",
    "post /lectures/{lecture_id}/study/generate", "get /study/items/{item_id}", "patch /study/items/{item_id}", "delete /study/items/{item_id}",
    "post /study/items/{item_id}/regenerate", "post /lectures/{lecture_id}/study/sessions", "post /courses/{course_id}/study/sessions",
    "get /study/sessions/{session_id}", "post /study/sessions/{session_id}/attempts", "post /study/sessions/{session_id}/finish",
    "get /lectures/{lecture_id}/study/progress", "get /courses/{course_id}/study/progress", "get /courses/{course_id}/overview",
    "get /courses/{course_id}/knowledge", "get /courses/{course_id}/concepts", "get /courses/{course_id}/search", "get /courses/{course_id}/study/items"])


class ApiBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        cls._old = os.environ.get("NOTIV_AUTH_SECRET")
        os.environ["NOTIV_AUTH_SECRET"] = SECRET
        from core.test_study import F
        # The lecture config is process-global and other test modules deliberately
        # reconfigure it. Rebind the shared study world before creating fixtures or
        # serving HTTP requests, otherwise routes can return 404 against another
        # module's repository when the full suite runs (the file passes in isolation).
        from core.lecture import config
        config.configure(W.env.repo, W.env.storage)
        cls.lid = W.new_lecture(ALICE, "API lecture", F.DL1, F.DL1_SLIDES, W.c_scratch)           # this class mutates it
        from server.dev_server import make_server
        cls.srv = make_server(0)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.tok = {u: create_token(u, SECRET, 3600) for u in (ALICE, BOB, EVE)}

    @classmethod
    def tearDownClass(cls):
        import os
        cls.srv.shutdown()
        os.environ.pop("NOTIV_AUTH_SECRET", None) if cls._old is None else os.environ.__setitem__("NOTIV_AUTH_SECRET", cls._old)

    def call(self, method, path, body=None, user=ALICE, raw=None):
        h = {"Content-Type": "application/json"}
        if user:
            h["Authorization"] = "Bearer " + self.tok[user]
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + path, data=data, headers=h, method=method)) as r:
                return r.status, json.loads(r.read()), r.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null"), e.headers


class TestAuthAndErrors(ApiBase):
    def paths(self):
        L, C = self.lid, W.c_dl
        item = self.call("GET", f"/lectures/{L}/study/items")[1]["items"][0]["item_id"]
        k = self.call("GET", f"/lectures/{L}/knowledge")[1]["items"][0]["knowledge_id"]
        sid = self.call("POST", f"/lectures/{L}/study/sessions", {"limit": 2})[1]["session_id"]
        return [("GET", f"/lectures/{L}/knowledge", None), ("GET", f"/lectures/{L}/concepts", None), ("POST", f"/lectures/{L}/knowledge/regenerate", {}),
                ("POST", f"/knowledge/{k}/review", {}), ("GET", f"/lectures/{L}/study/items", None), ("POST", f"/lectures/{L}/study/generate", {}),
                ("GET", f"/study/items/{item}", None), ("PATCH", f"/study/items/{item}", {"difficulty": "hard"}), ("POST", f"/study/items/{item}/regenerate", {}),
                ("POST", f"/lectures/{L}/study/sessions", {}), ("GET", f"/study/sessions/{sid}", None),
                ("POST", f"/study/sessions/{sid}/attempts", {"item_id": item, "self_grade": "know"}), ("POST", f"/study/sessions/{sid}/finish", {}),
                ("GET", f"/lectures/{L}/study/progress", None), ("GET", f"/courses/{C}/study/progress", None), ("POST", f"/courses/{C}/study/sessions", {}),
                ("GET", f"/courses/{C}/overview", None), ("GET", f"/courses/{C}/knowledge", None), ("GET", f"/courses/{C}/concepts", None),
                ("GET", f"/courses/{C}/search?q=momentum", None), ("GET", f"/courses/{C}/study/items", None),
                ("DELETE", f"/study/items/{item}", None), ("DELETE", f"/knowledge/{k}", None)]

    def test_every_endpoint_requires_auth(self):
        for m, p, b in self.paths():
            code, _, h = self.call(m, p, b, user=None)
            self.assertEqual((code, h["WWW-Authenticate"]), (401, "Bearer"), f"{m} {p}")

    def test_non_members_get_404_identical_to_missing_for_every_endpoint(self):
        for m, p, b in self.paths():
            for who in (BOB, EVE):
                c1, b1, _ = self.call(m, p, b, user=who)
                self.assertEqual(c1, 404, f"{who} {m} {p}")
                self.assertEqual(b1, {"detail": "not found"})

    def test_missing_ids_are_404(self):
        for m, p, b in (("GET", "/lectures/9999999/knowledge", None), ("GET", "/study/items/9999999", None), ("GET", "/study/sessions/9999999", None),
                        ("DELETE", "/knowledge/9999999", None), ("GET", "/courses/9999999/overview", None), ("POST", "/courses/9999999/study/sessions", {})):
            self.assertEqual(self.call(m, p, b)[0], 404, p)

    def test_bad_input(self):
        L = self.lid
        for path, body, want in ((f"/lectures/{L}/knowledge?kind=bogus", None, 400), (f"/lectures/{L}/study/items?kind=essay", None, 400),
                                 (f"/lectures/{L}/study/items?difficulty=x", None, 400), (f"/courses/{W.c_dl}/search?q=", None, 400),
                                 (f"/lectures/{L}/study/progress?x=1", None, 200)):
            self.assertEqual(self.call("GET", path, body)[0], want, path)
        self.assertEqual(self.call("POST", f"/lectures/{L}/study/generate", {"kinds": ["essay"]})[0], 400)
        self.assertEqual(self.call("POST", f"/lectures/{L}/study/sessions", {"limit": 0})[0], 400)
        self.assertEqual(self.call("POST", f"/lectures/{L}/study/sessions", {"limt": 3})[0], 422)               # unknown field
        self.assertEqual(self.call("POST", f"/lectures/{L}/study/sessions", raw=b"{not json")[0], 422)
        self.assertEqual(self.call("POST", f"/lectures/{L}/study/sessions", raw=b"[1,2]")[0], 422)


class TestStudyLifecycleOverHttp(ApiBase):
    def test_knowledge_and_concepts(self):
        code, k, _ = self.call("GET", f"/lectures/{self.lid}/knowledge")
        self.assertEqual((code, k["counts"]["definition"], k["stale"]), (200, 6, False))
        code, w, _ = self.call("GET", f"/lectures/{self.lid}/knowledge?kind=warning")
        self.assertEqual({i["kind"] for i in w["items"]}, {"warning"})
        code, c, _ = self.call("GET", f"/lectures/{self.lid}/concepts")
        bp = next(x for x in c["concepts"] if x["title"] == "Backpropagation")
        self.assertEqual(bp["definitions"][0]["evidence"][0]["citation"]["label"], "Slide 2 · 00:06")
        self.assertEqual(self.call("GET", f"/lectures/{self.lid}/knowledge?q=dropout")[1]["items"] != [], True)

    def test_items_hide_answers_and_reveal_on_request(self):
        code, hidden, _ = self.call("GET", f"/lectures/{self.lid}/study/items")
        self.assertEqual(code, 200)
        self.assertTrue(hidden["count"] > 30)
        self.assertTrue(all("answer" not in i and "evidence" not in i for i in hidden["items"]))
        code, shown, _ = self.call("GET", f"/lectures/{self.lid}/study/items?reveal=true&kind=mcq")
        self.assertTrue(all(i["answer"] and i["evidence"] and i["correct_option"] for i in shown["items"]))

    def test_edit_delete_regenerate(self):
        items = self.call("GET", f"/lectures/{self.lid}/study/items?kind=flashcard")[1]["items"]
        iid = next(i["item_id"] for i in items if i["subtype"] == "formula")
        code, e, _ = self.call("PATCH", f"/study/items/{iid}", {"prompt": "State the update rule.", "difficulty": "hard"})
        self.assertEqual((code, e["origin"], e["verified"], e["prompt"]), (200, "edited", False, "State the update rule."))
        for bad in ({"prompt": ""}, {"difficulty": "x"}, {"bogus": 1}, {"evidence": [{"evidence_id": 1}]}, {}):
            self.assertEqual(self.call("PATCH", f"/study/items/{iid}", bad)[0], 400, bad)
        code, r, _ = self.call("POST", f"/study/items/{iid}/regenerate", {})
        self.assertEqual((code, r["origin"], r["prompt"] != "State the update rule."), (200, "generated", True))
        code, d, _ = self.call("DELETE", f"/study/items/{iid}")
        self.assertEqual((code, d["deleted"]), (200, True))
        self.assertEqual(self.call("GET", f"/study/items/{iid}")[0], 404)
        self.assertEqual(self.call("DELETE", f"/study/items/{iid}")[0], 404)
        code, g, _ = self.call("POST", f"/lectures/{self.lid}/study/generate", {})
        self.assertEqual((code, g["created"]), (200, 0))                                                # deletion persisted through regeneration
        kid = next(k["knowledge_id"] for k in self.call("GET", f"/lectures/{self.lid}/knowledge?kind=emphasis")[1]["items"] if "Exam" in k["title"])
        iid2 = next(i["item_id"] for i in self.call("GET", f"/lectures/{self.lid}/study/items")[1]["items"] if i["subtype"] == "exam")
        self.assertEqual(self.call("DELETE", f"/knowledge/{kid}")[1]["deleted"], True)
        self.assertEqual(self.call("GET", f"/study/items/{iid2}")[0], 404)                              # derived generated item went with it

    def test_session_attempt_reveal_progress(self):
        code, s, _ = self.call("POST", f"/lectures/{self.lid}/study/sessions", {"kinds": ["mcq", "flashcard"], "limit": 4})
        self.assertEqual((code, s["summary"]["total"]), (200, 4))
        self.assertTrue(all("answer" not in i and "reveal" not in i for i in s["items"]))
        sid, mcq = s["session_id"], next(i for i in s["items"] if i["kind"] == "mcq")
        truth = self.call("GET", f"/study/items/{mcq['item_id']}")[1]
        code, a, _ = self.call("POST", f"/study/sessions/{sid}/attempts", {"item_id": mcq["item_id"], "selected_option": truth["correct_option"]})
        self.assertEqual((code, a["correct"], a["reveal"]["answer"] == truth["answer"], a["summary"]["answered"]), (200, True, True, 1))
        self.assertTrue(a["reveal"]["evidence"][0]["citation"]["label"])
        code, dup, _ = self.call("POST", f"/study/sessions/{sid}/attempts", {"item_id": mcq["item_id"], "selected_option": "a"})
        self.assertEqual((code, dup["detail"]["code"]), (409, "already_answered"))
        self.assertEqual(self.call("POST", f"/study/sessions/{sid}/attempts", {"item_id": mcq["item_id"] + 10 ** 6, "self_grade": "know"})[0], 404)
        card = next(i for i in s["items"] if i["kind"] == "flashcard")
        self.assertEqual(self.call("POST", f"/study/sessions/{sid}/attempts", {"item_id": card["item_id"], "self_grade": "maybe"})[0], 400)
        self.assertEqual(self.call("POST", f"/study/sessions/{sid}/attempts", {"item_id": card["item_id"], "self_grade": "know", "oops": 1})[0], 422)
        self.assertTrue(self.call("POST", f"/study/sessions/{sid}/attempts", {"item_id": card["item_id"], "self_grade": "know"})[1]["correct"])
        st = self.call("GET", f"/study/sessions/{sid}")[1]
        self.assertEqual((st["summary"]["answered"], st["summary"]["remaining"]), (2, 2))
        self.assertEqual(sum("reveal" in i for i in st["items"]), 2)                                    # only answered items reveal
        self.assertIsNotNone(self.call("POST", f"/study/sessions/{sid}/finish", {})[1]["finished_at"])
        p = self.call("GET", f"/lectures/{self.lid}/study/progress")[1]
        self.assertTrue(p["attempted"] >= 2 and p["accuracy"] is not None and p["sessions"]["finished"] >= 1)
        kid = self.call("GET", f"/lectures/{self.lid}/knowledge?kind=concept")[1]["items"][0]["knowledge_id"]
        self.assertEqual(self.call("POST", f"/knowledge/{kid}/review", {})[1]["count"], 1)
        self.assertEqual(self.call("GET", f"/lectures/{self.lid}/study/progress")[1]["concepts"]["reviewed"], 1)

    def test_sessions_are_private(self):
        sid = self.call("POST", f"/lectures/{self.lid}/study/sessions", {"limit": 1})[1]["session_id"]
        for who in (BOB, EVE):
            self.assertEqual(self.call("GET", f"/study/sessions/{sid}", user=who)[0], 404)
            self.assertEqual(self.call("POST", f"/study/sessions/{sid}/finish", {}, user=who)[0], 404)


class TestCourseMemoryOverHttp(ApiBase):
    def test_course_endpoints(self):
        c = W.c_dl
        code, ov, _ = self.call("GET", f"/courses/{c}/overview")
        self.assertEqual((code, [l["lecture_id"] for l in ov["lectures"]]), (200, [W.DL1, W.DL2]))
        code, k, _ = self.call("GET", f"/courses/{c}/knowledge?q=momentum")
        self.assertEqual({i["lecture_id"] for i in k["items"]}, {W.DL2})
        self.assertEqual(self.call("GET", f"/courses/{c}/knowledge?lecture={W.DB1}")[0], 404)
        sc = self.call("GET", f"/courses/{c}/concepts?shared_only=true")[1]
        self.assertEqual([x["title"] for x in sc["concepts"]], ["Learning rate"])
        r = self.call("GET", f"/courses/{c}/search?q=primary+key")[1]
        self.assertEqual((r["knowledge"], r["evidence"]), ([], []))
        r = self.call("GET", f"/courses/{W.c_db}/search?q=primary+key")[1]
        self.assertTrue(r["knowledge"] and r["evidence"])
        self.assertEqual({e["lecture_id"] for e in r["evidence"]}, {W.DB1})
        items = self.call("GET", f"/courses/{c}/study/items")[1]
        self.assertEqual({i["lecture_id"] for i in items["items"]}, {W.DL1, W.DL2})

    def test_course_session_and_progress(self):
        s = self.call("POST", f"/courses/{W.c_dl}/study/sessions", {"kinds": ["flashcard"], "limit": 5})[1]
        self.assertEqual({i["lecture_id"] for i in s["items"]} <= {W.DL1, W.DL2}, True)
        self.call("POST", f"/study/sessions/{s['session_id']}/attempts", {"item_id": s["items"][0]["item_id"], "self_grade": "know"})
        p = self.call("GET", f"/courses/{W.c_dl}/study/progress")[1]
        self.assertEqual((p["scope"]["type"], set(p["by_lecture"])), ("course", {str(W.DL1), str(W.DL2)}))
        self.assertGreaterEqual(p["attempted"], 1)

    def test_members_of_a_shared_course_and_outsiders(self):
        self.assertEqual(self.call("GET", f"/courses/{W.c_group}/overview", user=BOB)[0], 200)
        self.assertEqual(self.call("GET", f"/courses/{W.c_dl}/overview", user=BOB)[0], 404)
        self.assertEqual(self.call("GET", f"/courses/{W.c_bob}/overview", user=ALICE)[0], 404)
        sid = self.call("POST", f"/courses/{W.c_group}/study/sessions", {"limit": 2}, user=BOB)[1]["session_id"]
        self.assertEqual(self.call("GET", f"/study/sessions/{sid}", user=ALICE)[0], 404)             # even a fellow member can't read it


class TestStaticWiring(unittest.TestCase):
    def test_main_mounts_study_router_and_keeps_earlier_ones(self):
        src = open(_local_module_file("server.main"), encoding="utf-8").read()
        for r in ("routes_lectures", "routes_workspace", "routes_ask", "routes_study"):
            self.assertIn(f"app.include_router({r}.router)", src)

    def test_all_routes_exist_and_every_one_requires_auth(self):
        tree = ast.parse(open(_local_module_file("server.api.routes_study"), encoding="utf-8").read())
        fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.decorator_list and n.name != "_run"]
        self.assertEqual(sorted(f"{f.decorator_list[0].func.attr} {f.decorator_list[0].args[0].value}" for f in fns), EXPECTED_ROUTES)
        for fn in fns:
            self.assertIn("get_current_user", ast.unparse(fn.args), fn.name)

    def test_study_api_does_not_reach_the_legacy_stack(self):
        for start in ("server.api.routes_study", "server.api.study_handlers", "server.main"):
            bad = {m: by for m, by in import_closure(start).items() if m.startswith(FORBIDDEN_PREFIXES)}
            self.assertEqual(bad, {}, f"{start} reaches legacy modules: {bad}")

    def test_ui_scripts_have_no_duplicate_top_level_declarations(self):
        """A later declaration silently replaces an earlier one (this once broke the Summary tab); fail loudly instead.
        The UI is now ES modules: a duplicate top-level declaration is a SyntaxError, so check each module and forbid innerHTML."""
        import re
        root = os.path.dirname(_local_module_file("server.main")) + "/static"
        mods = sorted(f for f in os.listdir(root + "/ui") if f.endswith(".js"))
        self.assertTrue({"lecture.js", "lecture-tools.js", "course.js", "core.js"} <= set(mods))
        for name in mods:
            src = open(f"{root}/ui/{name}", encoding="utf-8").read()
            found = [a or b for a, b in re.findall(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)|^(?:export\s+)?(?:const|let|var)\s+(\w+)", src, re.M)]
            self.assertEqual(sorted({n for n in found if found.count(n) > 1}), [], name)
            self.assertNotIn("innerHTML", src, name)
        for page in ("workspace.html", "course.html", "library.html", "notebooks.html", "shared-notebook.html", "index.html", "sign-in.html"):
            self.assertNotRegex(open(f"{root}/{page}", encoding="utf-8").read(), r"<script(?![^>]*\bsrc=)[^>]*>\s*\S", page)  # no inline scripts (CSP script-src 'self')

    def test_workspace_ui_has_the_study_tab_and_course_page_is_served(self):
        import urllib.request
        from server.dev_server import make_server
        srv = make_server(0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{srv.server_address[1]}"
            self.assertIn("/static/ui/lecture.js", urllib.request.urlopen(base + "/app/lectures/1").read().decode())
            self.assertIn("/static/ui/course.js", urllib.request.urlopen(base + "/app/courses/1").read().decode())
            root = os.path.dirname(_local_module_file("server.main")) + "/static/ui"
            self.assertIn('["study", "Study"]', open(root + "/lecture.js", encoding="utf-8").read())
            self.assertIn("Concepts", open(root + "/course.js", encoding="utf-8").read())
        finally:
            srv.shutdown()

    def test_pipeline_builds_knowledge_during_analysis(self):
        job = W.repo.list_jobs(W.DL1)[-1]
        st = {s["stage"]: s for s in W.repo.list_stages(job["id"])}
        self.assertEqual(st["ANALYZE"]["status"], "completed")
        self.assertGreater(st["ANALYZE"]["output"]["knowledge"], 20)


@unittest.skipUnless(HAVE_FASTAPI, "fastapi/httpx not installed")
class TestFastApiStudyRoutes(ApiBase):
    """Runs only where FastAPI is installed (NOT run in the authoring sandbox)."""

    def test_routes_registered_validated_and_authorized(self):
        from fastapi.testclient import TestClient
        from server.main import app
        c = TestClient(app)
        from core.route_table import route_table
        paths = route_table(app)
        for spec in EXPECTED_ROUTES:
            m, p = spec.split(" ", 1)
            self.assertIn((p, m.upper()), paths)
        h = {"Authorization": "Bearer " + self.tok[ALICE]}
        self.assertEqual(c.get(f"/lectures/{self.lid}/knowledge").status_code, 401)
        self.assertEqual(c.post(f"/lectures/{self.lid}/study/sessions", json={"limit": 0}, headers=h).status_code, 422)
        self.assertEqual(c.get(f"/lectures/{self.lid}/knowledge", headers={"Authorization": "Bearer " + self.tok[BOB]}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
