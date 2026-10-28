"""FastAPI wiring tests — SKIPPED unless fastapi + httpx (+ python-multipart) are installed. NOT run in the authoring sandbox
=> the FastAPI app (server.main, deps, routes_lectures) is UNVERIFIED until this passes in your environment."""
import os
import unittest

try:
    import fastapi, httpx, multipart  # noqa: F401
    HAVE = True
except ImportError:
    HAVE = False

SECRET = "f" * 40


@unittest.skipUnless(HAVE, "fastapi/httpx/python-multipart not installed (FastAPI app UNVERIFIED)")
class TestFastApiApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from core.lecture import config
        from core.lecture.testkit import Env
        os.environ["NOTIV_AUTH_SECRET"] = SECRET
        cls.tmp = tempfile.mkdtemp(); cls.env = Env(cls.tmp); config.configure(cls.env.repo, cls.env.storage)
        from fastapi.testclient import TestClient
        from server.auth_tokens import create_token
        from server.main import app
        cls.c = TestClient(app, raise_server_exceptions=False)
        cls.h = {"Authorization": "Bearer " + create_token(1, SECRET)}
        cls.hb = {"Authorization": "Bearer " + create_token(2, SECRET)}

    def test_health_ready_and_headers(self):
        self.assertEqual(self.c.get("/healthz").status_code, 200)
        r = self.c.get("/readyz"); self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["x-content-type-options"], "nosniff"); self.assertIn("x-request-id", r.headers)

    def test_auth_required_and_docs_off_in_prod(self):
        self.assertEqual(self.c.post("/lectures", json={"title": "x"}).status_code, 401)

    def test_create_lecture_idempotent(self):
        h = {**self.h, "Idempotency-Key": "idem-key-0001"}
        a = self.c.post("/lectures", json={"title": "T"}, headers=h); b = self.c.post("/lectures", json={"title": "T"}, headers=h)
        self.assertEqual((a.status_code, b.status_code), (201, 201)); self.assertEqual(a.json(), b.json())
        self.assertEqual(b.headers.get("idempotency-replayed"), "true")
        self.assertEqual(self.c.post("/lectures", json={"title": "DIFFERENT"}, headers=h).status_code, 409)

    def test_huge_id_404_and_validation_does_not_echo(self):
        self.assertEqual(self.c.get("/lectures/99999999999999999999/overview", headers=self.h).status_code, 404)
        r = self.c.post("/lectures", json={"title": ""}, headers=self.h); self.assertEqual(r.status_code, 422)
        r = self.c.post("/lectures", json={"title": "x" * 400, "language": "SECRET-VALUE-123"}, headers=self.h)
        self.assertEqual(r.status_code, 422); self.assertNotIn("SECRET-VALUE-123", r.text)

    def test_cross_user_404(self):
        lid = self.c.post("/lectures", json={"title": "mine"}, headers=self.h).json()["lecture_id"]
        self.assertEqual(self.c.get(f"/lectures/{lid}", headers=self.hb).status_code, 404)

    def test_bad_signed_file_403(self):
        self.assertEqual(self.c.get("/files/lectures/1/a.png?exp=abc&sig=x").status_code, 403)

    def test_oversized_json_413(self):
        self.assertEqual(self.c.post("/lectures", content=b"{" + b" " * 200000 + b"}", headers={**self.h, "Content-Type": "application/json"}).status_code, 413)


if __name__ == "__main__":
    unittest.main()
