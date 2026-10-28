"""Sprint 2 API-layer tests.

* handlers: error contract (401 is the route layer's; 404/409/400 here)
* real HTTP via server.dev_server (same handlers/auth/storage as the FastAPI
  routes): auth, status codes, JSON shapes, media Range requests, signed files
* static guarantees: new router is mounted, old search route retired, import
  isolation from the legacy stack
* FastAPI-level route tests run only when fastapi + httpx are installed
"""
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
from core.lecture.make_test_lecture import make_lecture, make_text_pdf
from core.lecture.testkit import Env, segments
from core.test_api_boundary import FORBIDDEN_PREFIXES, ROOT, _local_module_file, import_closure
from core.test_workspace import SEGS
from server.api import workspace_handlers as H
from server.auth_tokens import create_token

try:
    import fastapi  # noqa: F401
    import httpx  # noqa: F401
    HAVE_FASTAPI = True
except ImportError:
    HAVE_FASTAPI = False

SECRET = "s" * 40
ALICE, BOB = 1, 2


class ApiBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._old_secret = os.environ.get("NOTIV_AUTH_SECRET")
        os.environ["NOTIV_AUTH_SECRET"] = SECRET
        cls.tmp = tempfile.mkdtemp()
        video, _, _ = make_lecture(os.path.join(cls.tmp, "fx"), [6, 9, 5, 7])
        pdf = make_text_pdf(os.path.join(cls.tmp, "fx"))
        cls.env = Env(cls.tmp)
        config.configure(cls.env.repo, cls.env.storage)
        cls.lid = cls.env.lecture(ALICE, video, pdf, title="API lecture")
        cls.env.process(cls.lid, segments(*SEGS))
        cls.queued = cls.env.lectures.create(ALICE, "queued")
        cls.env.jobs().enqueue(cls.queued)

    @classmethod
    def tearDownClass(cls):
        if cls._old_secret is None:
            os.environ.pop("NOTIV_AUTH_SECRET", None)
        else:
            os.environ["NOTIV_AUTH_SECRET"] = cls._old_secret
        shutil.rmtree(cls.tmp, ignore_errors=True)


class TestHandlers(ApiBase):
    def test_happy_paths_return_json_serializable_data(self):
        for fn, kw in ((H.overview, {}), (H.timeline, {}), (H.transcript, {}), (H.slides, {}), (H.evidence, {}),
                       (H.key_moments, {}), (H.summary, {}), (H.search, {"q": "kernel"}), (H.position, {"t": 3.0})):
            json.dumps(fn(ALICE, self.lid, **kw))

    def test_error_contract(self):
        def status(fn, uid, lid, **kw):
            with self.assertRaises(H.ApiError) as c:
                fn(uid, lid, **kw)
            return c.exception.status, c.exception.detail
        self.assertEqual(status(H.overview, BOB, self.lid), (404, "not found"))
        self.assertEqual(status(H.overview, ALICE, 10 ** 6), (404, "not found"))
        s, d = status(H.transcript, ALICE, self.queued)
        self.assertEqual((s, d["state"], d["stage"] is None or isinstance(d["stage"], str)), (409, "queued", True))
        self.assertIn("progress_pct", d)
        self.assertEqual(status(H.search, ALICE, self.lid, q="")[0], 400)
        self.assertEqual(status(H.position, ALICE, self.lid)[0], 400)
        self.assertEqual(status(H.position, ALICE, self.lid, slide=99)[0], 404)
        self.assertEqual(status(H.evidence_item, ALICE, self.lid, evidence_id=10 ** 6)[0], 404)


class TestHttp(ApiBase):
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

    def get(self, path, user=ALICE, headers=None, raw=False):
        h = dict(headers or {})
        if user:
            h["Authorization"] = "Bearer " + self.tok[user]
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + path, headers=h)) as r:
                body = r.read()
                return r.status, (body if raw else json.loads(body)), r.headers
        except urllib.error.HTTPError as e:
            body = e.read()
            return e.code, (body if raw else json.loads(body or b"null")), e.headers

    def test_every_endpoint_requires_auth(self):
        for p in ("overview", "timeline", "transcript", "slides", "evidence", "key-moments", "summary", "search?q=a", "position?t=1"):
            code, body, hdr = self.get(f"/lectures/{self.lid}/{p}", user=None)
            self.assertEqual(code, 401, p)
            self.assertEqual(hdr["WWW-Authenticate"], "Bearer")

    def test_other_users_get_404_and_identical_body_to_missing(self):
        for p in ("overview", "timeline", "transcript", "slides", "evidence", "key-moments", "summary", "search?q=gradient", "position?t=1"):
            c1, b1, _ = self.get(f"/lectures/{self.lid}/{p}", user=BOB)
            c2, b2, _ = self.get(f"/lectures/999999/{p}", user=BOB)
            self.assertEqual((c1, b1), (c2, b2), p)
            self.assertEqual(c1, 404)

    def test_endpoints_over_http(self):
        code, ov, _ = self.get(f"/lectures/{self.lid}/overview")
        self.assertEqual((code, ov["state"], ov["counts"]["slides"]), (200, "ready", 4))
        code, sr, _ = self.get(f"/lectures/{self.lid}/search?q=chain+rule&limit=5")
        self.assertEqual((code, sr["results"][0]["citation"]["label"]), (200, "Slide 2 · 00:07"))
        code, body, _ = self.get(f"/lectures/{self.lid}/search?q=")
        self.assertEqual((code, body["detail"]), (400, "query required"))
        code, pos, _ = self.get(f"/lectures/{self.lid}/position?evidence={sr['results'][0]['evidence_id']}")
        self.assertEqual((code, pos["seek_seconds"], pos["slide_index"]), (200, 7.0, 2))
        code, tr, _ = self.get(f"/lectures/{self.lid}/transcript?start=10&end=17")
        self.assertEqual([s["start"] for s in tr["segments"]], [7.0, 16.0])
        code, body, _ = self.get(f"/lectures/{self.queued}/key-moments")
        self.assertEqual((code, body["detail"]["state"]), (409, "queued"))
        code, body, _ = self.get(f"/lectures/{self.lid}/position?t=abc")
        self.assertEqual(code, 422)

    def test_media_url_supports_range_seeking_and_rejects_tampering(self):
        _, ov, _ = self.get(f"/lectures/{self.lid}/overview")
        url = ov["media"]["url"]
        code, full, h = self.get(url, user=None, raw=True)                       # signed URL: no bearer needed
        self.assertEqual(code, 200)
        self.assertEqual(h["Accept-Ranges"], "bytes")
        code, part, h = self.get(url, user=None, raw=True, headers={"Range": "bytes=100-199"})
        self.assertEqual((code, len(part), part == full[100:200]), (206, 100, True))
        self.assertEqual(h["Content-Range"], f"bytes 100-199/{len(full)}")
        code, part, _ = self.get(url, user=None, raw=True, headers={"Range": "bytes=-50"})
        self.assertEqual((code, part == full[-50:]), (206, True))
        code, _, _ = self.get(url.replace("sig=", "sig=0"), user=None, raw=True)
        self.assertEqual(code, 403)
        code, _, _ = self.get(url.split("?")[0], user=None, raw=True)             # unsigned
        self.assertEqual(code, 403)
        code, img, _ = self.get(self.get(f"/lectures/{self.lid}/slides")[1]["slides"][0]["image_url"], user=None, raw=True)
        self.assertEqual((code, img[:8]), (200, b"\x89PNG\r\n\x1a\n"))

    def test_ui_page_is_served_and_has_no_inline_html_injection_of_data(self):
        code, page, h = self.get("/app/lectures/1", user=None, raw=True)
        self.assertEqual((code, h["Content-Type"]), (200, "text/html"))
        src = page.decode()
        self.assertNotIn(".innerHTML", src)                                       # lecture text is only ever set via textContent / text nodes
        self.assertNotIn("insertAdjacentHTML", src)
        self.assertNotIn("document.write", src)


class TestStaticWiring(unittest.TestCase):
    def test_main_mounts_both_routers_and_keeps_the_sprint1_mount_line(self):
        src = open(_local_module_file("server.main"), encoding="utf-8").read()
        self.assertIn("app.include_router(routes_lectures.router)", src)          # asserted by the Sprint 1 boundary test
        self.assertIn("app.include_router(routes_workspace.router)", src)

    def test_old_search_route_is_retired_so_paths_do_not_collide(self):
        old = open(_local_module_file("server.api.routes_lectures"), encoding="utf-8").read()
        self.assertNotIn('"/lectures/{lecture_id}/search"', old.replace("# GET /lectures/{id}/search", ""))
        new = open(_local_module_file("server.api.routes_workspace"), encoding="utf-8").read()
        self.assertEqual(new.count('"/lectures/{lecture_id}/search"'), 1)

    def test_all_requested_endpoints_exist(self):
        src = open(_local_module_file("server.api.routes_workspace"), encoding="utf-8").read()
        for p in ("overview", "timeline", "transcript", "slides", "evidence", "search", "key-moments"):
            self.assertIn(f'@router.get("/lectures/{{lecture_id}}/{p}")', src)

    def test_workspace_api_does_not_reach_the_legacy_stack(self):
        for start in ("server.api.routes_workspace", "server.api.workspace_handlers", "server.main"):
            closure = import_closure(start)
            bad = {m: by for m, by in closure.items() if m.startswith(FORBIDDEN_PREFIXES)}
            self.assertEqual(bad, {}, f"{start} reaches legacy modules: {bad}")

    def test_every_local_import_resolves(self):
        missing = [m for m in import_closure("server.api.routes_workspace")
                   if m.split(".")[0] in ("server", "core") and _local_module_file(m) is None
                   and _local_module_file(m.rpartition(".")[0] or m) is None]
        self.assertEqual(missing, [])

    def test_every_route_declares_the_auth_dependency(self):
        import ast
        tree = ast.parse(open(_local_module_file("server.api.routes_workspace"), encoding="utf-8").read())
        fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.decorator_list and n.name != "_run"]
        self.assertEqual(len(fns), 10)
        for fn in fns:
            self.assertIn("get_current_user", ast.unparse(fn.args), fn.name)       # no endpoint can be added without auth


@unittest.skipUnless(HAVE_FASTAPI, "fastapi/httpx not installed")
class TestFastApiRoutes(ApiBase):
    """Runs only where FastAPI is installed (NOT run in the authoring sandbox)."""

    def test_routes_registered_and_auth_enforced_and_errors_mapped(self):
        from fastapi.testclient import TestClient
        from server.main import app
        from core.route_table import route_paths
        paths = route_paths(app)
        for p in ("overview", "timeline", "transcript", "slides", "evidence", "search", "key-moments", "summary", "position"):
            self.assertIn(f"/lectures/{{lecture_id}}/{p}", paths)
        c = TestClient(app)
        self.assertEqual(c.get(f"/lectures/{self.lid}/overview").status_code, 401)
        h = {"Authorization": "Bearer " + create_token(ALICE, SECRET, 3600)}
        self.assertEqual(c.get(f"/lectures/{self.lid}/overview", headers=h).json()["state"], "ready")
        self.assertEqual(c.get(f"/lectures/{self.queued}/summary", headers=h).status_code, 409)
        self.assertEqual(c.get(f"/lectures/{self.lid}/search?q=", headers=h).status_code, 422)     # FastAPI min_length
        self.assertEqual(c.get(f"/lectures/{self.lid}/search?q=kernel&source_type=bogus", headers=h).status_code, 422)
        hb = {"Authorization": "Bearer " + create_token(BOB, SECRET, 3600)}
        self.assertEqual(c.get(f"/lectures/{self.lid}/overview", headers=hb).status_code, 404)
        self.assertEqual(c.get("/app/lectures/1").status_code, 200)


if __name__ == "__main__":
    unittest.main()
