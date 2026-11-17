"""Static UI delivery + privacy: CSP on every page, no inline script, no third-party origins, original hero bytes, and erasure of notebooks/share links."""
import os
import re
import tempfile
import unittest

from fastapi.testclient import TestClient

from core.lecture import config, ops_store, store
from core.lecture.testkit import Env
from server.api import notebooks_service as nbs
from server.auth_tokens import create_token
from server.main import app

SECRET = "ui-security-test-secret-which-is-long-enough-12"
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", "server", "static")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.env = Env(self.tmp.name); config.configure(self.env.repo, self.env.storage)
        self._old = os.environ.get("NOTIV_AUTH_SECRET"); os.environ["NOTIV_AUTH_SECRET"] = SECRET
        self.c = TestClient(app); self.h = {"Authorization": "Bearer " + create_token(11, SECRET, 3600)}

    def tearDown(self):
        os.environ.pop("NOTIV_AUTH_SECRET", None) if self._old is None else os.environ.__setitem__("NOTIV_AUTH_SECRET", self._old); self.tmp.cleanup()


class TestPages(Base):
    PAGES = ["/", "/sign-in", "/app", "/app/notebooks", "/app/notebooks/1", "/app/lectures/1", "/app/courses/1", "/share/sometoken"]

    def test_every_page_is_served_with_a_strict_csp_and_no_inline_script(self):
        for p in self.PAGES:
            r = self.c.get(p); self.assertEqual(r.status_code, 200, p)
            csp = r.headers.get("content-security-policy", ""); self.assertIn("script-src 'self'", csp, p); self.assertNotIn("unsafe-inline'; style", csp.split("script-src")[1][:30])
            self.assertIn("frame-ancestors 'none'", csp, p); self.assertIn("object-src 'none'", csp, p)
            self.assertNotRegex(r.text, r"<script(?![^>]*\bsrc=)[^>]*>\s*\S", p); self.assertNotRegex(r.text, r"\son\w+\s*=", p)
            self.assertNotIn("googleapis", r.text, p); self.assertNotIn("cdn.", r.text, p)

    def test_share_page_is_not_cached_or_indexed(self):
        r = self.c.get("/share/abc"); self.assertEqual(r.headers.get("cache-control"), "no-store"); self.assertIn("noindex", r.text)

    def test_every_asset_a_page_references_exists_and_is_served(self):
        seen = set()
        for p in self.PAGES:
            for ref in re.findall(r'(?:src|href)="(/static/[^"#?]+)"', self.c.get(p).text):
                if ref in seen: continue
                seen.add(ref); self.assertEqual(self.c.get(ref).status_code, 200, ref)
        self.assertIn("/static/assets/notiv.jpg", seen)

    def test_every_module_import_resolves(self):
        ui = os.path.join(STATIC, "ui"); mods = {f for f in os.listdir(ui) if f.endswith(".js")}
        for f in mods:
            for imp in re.findall(r'from "(/static/ui/[^"]+)"', open(os.path.join(ui, f), encoding="utf-8").read()):
                self.assertEqual(self.c.get(imp).status_code, 200, f"{f} -> {imp}")

    def test_fonts_are_self_hosted(self):
        css = open(os.path.join(STATIC, "ui", "base.css"), encoding="utf-8").read()
        for u in re.findall(r"url\(([^)]+)\)", css):
            u = u.strip("'\""); self.assertTrue(u.startswith(("/static/", "data:")), u); 
            if u.startswith("/static/"): self.assertEqual(self.c.get(u).status_code, 200, u)

    def test_landing_shows_the_real_app(self):
        landing = self.c.get("/").text
        self.assertIn('src="/static/assets/notiv.jpg"', landing)
        for shot in ("/static/assets/app-preview.png", "/static/assets/app-phone.png"):
            self.assertIn(f'src="{shot}"', landing); r = self.c.get(shot)
            self.assertEqual(r.status_code, 200, shot); self.assertTrue(r.content.startswith(b"\x89PNG"), shot)
        self.assertNotRegex(open(os.path.join(STATIC, "ui", "landing.css"), encoding="utf-8").read(), r"filter\s*:|backdrop-filter|mix-blend")

    def test_no_glassmorphism(self):
        css = "".join(open(os.path.join(STATIC, "ui", f), encoding="utf-8").read() for f in os.listdir(os.path.join(STATIC, "ui")) if f.endswith(".css"))
        self.assertNotIn("backdrop-filter", css)                                   # no glassmorphism


class TestErasure(Base):
    def test_erasing_a_user_removes_notebooks_and_their_public_share_links(self):
        nb = nbs.create_notebook(11, "To be erased"); sh = nbs.create_share(11, nb["id"])
        self.assertEqual(self.c.get(f"/shared/notebooks/{sh['token']}").status_code, 200)
        other = nbs.create_notebook(22, "Someone else's"); osh = nbs.create_share(22, other["id"])
        ops_store.purge_user_rows(11)
        self.assertEqual(self.c.get(f"/shared/notebooks/{sh['token']}").status_code, 404)
        self.assertEqual(self.c.get(f"/shared/notebooks/{osh['token']}").status_code, 200)       # other people's links untouched
        self.assertEqual(self.c.get("/notebooks", headers=self.h).json()["items"], [])

    def test_erasing_removes_the_sign_in_identity_and_user_row(self):
        c = store.conn(); c.execute("INSERT INTO users(id,email,display_name,created_at,last_login_at) VALUES(?,?,?,?,?)", (11, "a@b", "A", 1, 1))
        c.execute("INSERT INTO auth_identities(user_id,issuer,subject,email,created_at) VALUES(?,?,?,?,?)", (11, "https://i", "s", "a@b", 1)); c.commit()
        ops_store.purge_user_rows(11)
        self.assertEqual(c.execute("SELECT COUNT(*) AS n FROM auth_identities WHERE user_id=11").fetchone()["n"], 0)
        self.assertEqual(c.execute("SELECT COUNT(*) AS n FROM users WHERE id=11").fetchone()["n"], 0)


class TestPdfExport(Base):
    """Downloads are PDF only, and every item type is laid out as readable text, never raw JSON."""
    def test_every_item_type_prints_readably(self):
        import io
        from pypdf import PdfReader
        pdf_text = lambda data: " ".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)
        nb = nbs.create_notebook(11, "Week 6 — caching")
        ev = [{"citation": {"label": "p. 3"}, "available": True}]
        items = [
            ("note", "My note", "# Heading\n- **bold** point\n1. first step"),
            ("summary", "Summary", {"mode": "ai", "overview": "Caches trade memory for latency.", "takeaways": [{"text": "LRU evicts the oldest", "evidence": ev}],
                                     "sections": [{"title": "Eviction", "body": "TTL expires entries.", "evidence": ev}]}),
            ("concepts", "Concepts", {"items": [{"title": "Consistent hashing", "body": "Only K/N keys move.", "evidence": ev}]}),
            ("flashcards", "Cards", {"items": [{"prompt": "What is LRU?", "answer": "Least recently used"}, {"prompt": "Q2", "answer": "A2"}]}),
            ("quiz", "Quiz", {"items": [{"prompt": "Which moves fewer keys?", "options": [{"id": "a", "text": "Modulo"}, {"id": "b", "text": "Ring"}], "correct_option": "b", "explanation": "Only K/N move."}]}),
            ("mind_map", "Map", {"nodes": [{"id": "1", "text": "Cache", "x": 0, "y": 0}, {"id": "2", "text": "Eviction", "x": 300, "y": 120}], "edges": [{"from": "1", "to": "2", "label": "needs"}]}),
            ("summary", "Broken legacy", "not a dict"),
        ]
        ids = {}
        for t, title, content in items:
            r = self.c.post(f"/notebooks/{nb['id']}/items", headers=self.h, json={"item_type": t, "title": title, "content": content})
            self.assertEqual(r.status_code, 201, r.text); ids[title] = r.json()["id"]
        r = self.c.get(f"/notebooks/{nb['id']}/export", headers=self.h)
        self.assertEqual(r.status_code, 200, r.text[:300]); self.assertEqual(r.headers["content-type"], "application/pdf")
        text = pdf_text(r.content)
        for want in ("Week 6", "Heading", "bold point", "first step", "Caches trade memory", "Key takeaways", "LRU evicts", "p. 3",
                     "Consistent hashing", "What is LRU?", "Least recently used", "Which moves fewer keys?", "Answer key", "Ring",
                     "Cache", "Eviction", "needs", "Broken legacy"):
            self.assertIn(want, text)
        for raw in ('"prompt"', '"items"', "{'", '"evidence"'):
            self.assertNotIn(raw, text)
        one = self.c.get(f"/notebooks/{nb['id']}/items/{ids['Cards']}/export", headers=self.h)
        self.assertEqual(one.status_code, 200); self.assertIn("Cards.pdf", one.headers["content-disposition"])
        self.assertIn("Least recently used", pdf_text(one.content))

if __name__ == "__main__":
    unittest.main()
