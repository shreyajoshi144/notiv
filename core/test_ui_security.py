"""Static UI delivery + privacy: CSP on every page, no inline script, no third-party origins, original hero bytes, and erasure of notebooks/share links."""
import hashlib
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
HERO_SHA = "7ed3a0e023612b3ae4e27b2f0703ec1e07fbd23fc902fa4805f319d80e540e63"


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
        self.assertIn("/static/assets/notiv-hero.jpg", seen)

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

    def test_hero_photograph_is_the_original_bytes(self):
        r = self.c.get("/static/assets/notiv-hero.jpg"); self.assertEqual(hashlib.sha256(r.content).hexdigest(), HERO_SHA)
        landing = self.c.get("/").text; self.assertIn('src="/static/assets/notiv-hero.jpg"', landing)
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


class TestCsvExport(Base):
    def test_flashcard_and_quiz_csv_has_one_row_per_card(self):
        import csv, io
        nb = nbs.create_notebook(11, "CSV")
        for t, items in (("flashcards", [{"prompt": "Q1", "answer": "A1"}, {"prompt": "Q2", "answer": "A2"}]), ("quiz", [{"prompt": "Q3", "options": [{"id": "a", "text": "x"}], "correct_option": "a"}])):
            self.c.post(f"/notebooks/{nb['id']}/items", headers=self.h, json={"item_type": t, "title": t, "content": {"items": items}})
        rows = list(csv.reader(io.StringIO(self.c.get(f"/notebooks/{nb['id']}/export?format=csv", headers=self.h).content.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 1 + 3, rows)
        body = " ".join(r[2] for r in rows[1:]); [self.assertIn(q, body) for q in ("Q1", "Q2", "Q3")]


if __name__ == "__main__":
    unittest.main()
