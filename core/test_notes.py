"""Personal notes (gap closure): CRUD + persistence + lecture/evidence links + privacy + deletion cleanup + HTTP contract.

Real code paths only (real SQLite store, real services, real stdlib HTTP server). The FastAPI routes are covered by the
class at the bottom, which SKIPS when FastAPI/httpx are missing (=> routes_notes.py is UNVERIFIED in that case)."""
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from core.lecture import config, maintenance, notes, store
from core.lecture.errors import NotFoundError, QuotaError, ValidationError
from core.lecture.testkit import Env
from server.auth_tokens import create_token

ALICE, BOB, EVE = 1, 2, 3
SECRET = "n" * 40
HUGE = "99999999999999999999999"


class NotesBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = Env(self.tmp)
        self.repo = self.env.repo
        self._old = (config._repo, config._storage)
        config.configure(self.repo, self.env.storage)
        # shared workspace: ALICE owns, BOB member, EVE outsider
        self.ws = self.repo.create_workspace(ALICE, "Shared")
        self.repo.add_member(self.ws, BOB)
        self.lid = self.env.lectures.create(ALICE, "L1", workspace_id=self.ws)
        self.lid2 = self.env.lectures.create(ALICE, "L2", workspace_id=self.ws)
        self.ev = self.repo.replace_evidence(self.lid, [
            {"source_type": "transcript", "source_id": 1, "text": "Gradient descent minimises the loss.", "start": 3.0, "end": 6.0},
            {"source_type": "slide", "source_id": 2, "text": "Backpropagation computes gradients.", "slide_index": 2}])
        self.ev2 = self.repo.replace_evidence(self.lid2, [{"source_type": "transcript", "source_id": 1, "text": "Other lecture text.", "start": 1.0, "end": 2.0}])
        self.svc = notes.NotesService(self.repo)

    def tearDown(self):
        config._repo, config._storage = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestNotesService(NotesBase):
    def test_crud_roundtrip_with_evidence_links(self):
        n = self.svc.create(ALICE, self.lid, "  my note  ", evidence_ids=[self.ev[0], self.ev[1]], slide_index=2, t=4.5)
        self.assertEqual((n["body"], n["slide_index"], n["t"], n["lecture_id"]), ("my note", 2, 4.5, self.lid))
        self.assertEqual([l["evidence_id"] for l in n["links"]], sorted(self.ev))
        self.assertTrue(all(l["available"] and not l["source_changed"] for l in n["links"]))
        self.assertIn("Gradient descent", n["links"][0]["quote"])
        u = self.svc.update(ALICE, n["id"], body="edited", evidence_ids=[self.ev[1]], t=None)
        self.assertEqual((u["body"], [l["evidence_id"] for l in u["links"]], u["t"]), ("edited", [self.ev[1]], None))
        self.assertGreaterEqual(u["updated_at"], n["updated_at"])
        self.assertEqual(self.svc.list(ALICE, self.lid)["notes"][0]["id"], n["id"])
        self.svc.delete(ALICE, n["id"])
        with self.assertRaises(NotFoundError): self.svc.get(ALICE, n["id"])
        self.assertEqual(self.svc.list(ALICE, self.lid)["notes"], [])
        self.assertEqual(store.conn().execute("SELECT COUNT(*) FROM user_note_links").fetchone()[0], 0)

    def test_notes_persist_across_a_fresh_connection(self):
        n = self.svc.create(ALICE, self.lid, "durable", evidence_ids=[self.ev[0]])
        store._local.c = None                                        # drop the cached connection == process restart
        again = notes.NotesService(self.repo).get(ALICE, n["id"])
        self.assertEqual((again["body"], len(again["links"])), ("durable", 1))

    def test_pagination_and_evidence_filter(self):
        ids = [self.svc.create(ALICE, self.lid, f"n{i}", evidence_ids=[self.ev[0]] if i % 2 == 0 else None)["id"] for i in range(5)]
        p1 = self.svc.list(ALICE, self.lid, limit=2)
        self.assertEqual([n["id"] for n in p1["notes"]], ids[:2]); self.assertTrue(p1["has_more"])
        p2 = self.svc.list(ALICE, self.lid, after_id=p1["next_after_id"], limit=10)
        self.assertEqual([n["id"] for n in p2["notes"]], ids[2:]); self.assertFalse(p2["has_more"])
        linked = self.svc.list(ALICE, self.lid, evidence_id=self.ev[0])["notes"]
        self.assertEqual([n["id"] for n in linked], [ids[0], ids[2], ids[4]])

    # ── privacy / authorization ──────────────────────────────────────────
    def test_notes_are_private_even_inside_a_shared_workspace(self):
        n = self.svc.create(ALICE, self.lid, "alice only")
        self.assertEqual(self.svc.list(BOB, self.lid)["notes"], [])                   # BOB may open the lecture, not ALICE's notes
        for op in (lambda: self.svc.get(BOB, n["id"]), lambda: self.svc.update(BOB, n["id"], body="x"),
                   lambda: self.svc.delete(BOB, n["id"])):
            with self.assertRaises(NotFoundError): op()
        self.assertEqual(self.svc.get(ALICE, n["id"])["body"], "alice only")             # untouched
        with self.assertRaises(NotFoundError): self.svc.create(EVE, self.lid, "outsider")   # no lecture access
        with self.assertRaises(NotFoundError): self.svc.list(EVE, self.lid)

    def test_missing_and_foreign_notes_are_indistinguishable(self):
        n = self.svc.create(ALICE, self.lid, "x")
        msgs = set()
        for uid, nid in ((BOB, n["id"]), (ALICE, n["id"] + 1000), (ALICE, 0), (ALICE, -5), (ALICE, 10 ** 30)):
            with self.assertRaises(NotFoundError) as cm: self.svc.get(uid, nid)
            msgs.add(str(cm.exception))
        self.assertEqual(msgs, {"not found"})

    def test_author_who_loses_workspace_access_loses_the_notes(self):
        n = self.svc.create(BOB, self.lid, "bob note")
        store.conn().execute("DELETE FROM workspace_members WHERE workspace_id=? AND user_id=?", (self.ws, BOB)); store.conn().commit()
        with self.assertRaises(NotFoundError): self.svc.get(BOB, n["id"])
        with self.assertRaises(NotFoundError): self.svc.list(BOB, self.lid)

    # ── evidence-link integrity ──────────────────────────────────────────
    def test_cannot_link_evidence_from_another_lecture_or_nonexistent(self):
        msgs = set()
        for bad in ([self.ev2[0]], [999999], [self.ev[0], self.ev2[0]]):
            with self.assertRaises(ValidationError) as cm: self.svc.create(ALICE, self.lid, "x", evidence_ids=bad)
            msgs.add(str(cm.exception))
        self.assertEqual(msgs, {"unknown evidence id"})                                  # no oracle for other lectures' ids
        self.assertEqual(self.svc.list(ALICE, self.lid)["notes"], [])                    # nothing half-created
        n = self.svc.create(ALICE, self.lid, "ok")
        with self.assertRaises(ValidationError): self.svc.update(ALICE, n["id"], evidence_ids=[self.ev2[0]])

    def test_link_survives_reprocessing_and_reports_source_replaced(self):
        n = self.svc.create(ALICE, self.lid, "about gradients", evidence_ids=[self.ev[0]])
        new_ids = self.repo.replace_evidence(self.lid, [{"source_type": "transcript", "source_id": 1, "text": "Totally different.", "start": 1.0, "end": 2.0}])
        self.assertNotIn(self.ev[0], new_ids)
        lk = self.svc.get(ALICE, n["id"])["links"][0]
        self.assertFalse(lk["available"])
        self.assertIn("Gradient descent", lk["quote"])                                    # snapshot kept for the UI
        self.assertEqual(lk["start"], 3.0)

    def test_source_changed_flag_when_evidence_text_is_edited(self):
        n = self.svc.create(ALICE, self.lid, "x", evidence_ids=[self.ev[0]])
        c = store.conn(); c.execute("UPDATE lecture_evidence SET text='Corrected text.' WHERE id=?", (self.ev[0],)); c.commit()
        lk = self.svc.get(ALICE, n["id"])["links"][0]
        self.assertTrue(lk["available"] and lk["source_changed"])
        self.assertEqual(lk["quote"], "Corrected text.")

    # ── validation ───────────────────────────────────────────────────────
    def test_input_validation(self):
        bad = [dict(body=""), dict(body="   "), dict(body=None), dict(body=5), dict(body="x" * (notes.MAX_BODY + 1)),
               dict(body="x", slide_index=0), dict(body="x", slide_index=True), dict(body="x", slide_index="2"),
               dict(body="x", t=-1), dict(body="x", t=float("nan")), dict(body="x", t=float("inf")), dict(body="x", t=True),
               dict(body="x", evidence_ids="1"), dict(body="x", evidence_ids=[True]), dict(body="x", evidence_ids=[0]),
               dict(body="x", evidence_ids=[2 ** 63])]
        for kw in bad:
            with self.assertRaises(ValidationError, msg=str(kw)): self.svc.create(ALICE, self.lid, **kw)
        self.assertEqual(self.svc.create(ALICE, self.lid, "x" * notes.MAX_BODY)["body"], "x" * notes.MAX_BODY)
        n = self.svc.create(ALICE, self.lid, "ok")
        for kw in (dict(), dict(nope=1), dict(body="")):
            with self.assertRaises(ValidationError, msg=str(kw)): self.svc.update(ALICE, n["id"], **kw)
        with self.assertRaises(ValidationError): self.svc.list(ALICE, self.lid, after_id=-1)
        with self.assertRaises(ValidationError): self.svc.list(ALICE, self.lid, evidence_id=0)

    def test_too_many_links_rejected(self):
        many = [self.ev[0]] * 3
        self.assertEqual(len(self.svc.create(ALICE, self.lid, "dupes collapse", evidence_ids=many)["links"]), 1)
        with mock.patch.object(notes, "MAX_LINKS", 1):
            with self.assertRaises(ValidationError): self.svc.create(ALICE, self.lid, "x", evidence_ids=self.ev)

    def test_quota_per_lecture(self):
        with mock.patch.dict(os.environ, {"NOTIV_QUOTA_NOTES_PER_LECTURE": "2"}):
            self.svc.create(ALICE, self.lid, "1"); self.svc.create(ALICE, self.lid, "2")
            with self.assertRaises(QuotaError): self.svc.create(ALICE, self.lid, "3")
            self.svc.create(ALICE, self.lid2, "other lecture is separate")
            self.svc.create(BOB, self.lid, "other user is separate")

    def test_markup_is_stored_verbatim_as_text(self):
        n = self.svc.create(ALICE, self.lid, "<script>alert(1)</script> & <b>")
        self.assertEqual(self.svc.get(ALICE, n["id"])["body"], "<script>alert(1)</script> & <b>")

    # ── deletion cleanup ─────────────────────────────────────────────────
    def test_deleting_a_lecture_deletes_its_notes_and_links(self):
        keep = self.svc.create(ALICE, self.lid2, "keep")
        self.svc.create(ALICE, self.lid, "a", evidence_ids=[self.ev[0]]); self.svc.create(BOB, self.lid, "b", evidence_ids=[self.ev[1]])
        self.repo.delete_lecture_data(self.lid)
        c = store.conn()
        self.assertEqual(c.execute("SELECT COUNT(*) FROM user_notes WHERE lecture_id=?", (self.lid,)).fetchone()[0], 0)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM user_note_links").fetchone()[0], 0)
        self.assertEqual(self.svc.get(ALICE, keep["id"])["body"], "keep")

    def test_right_to_erasure_removes_the_users_notes_everywhere(self):
        self.svc.create(BOB, self.lid, "bob1", evidence_ids=[self.ev[0]]); self.svc.create(BOB, self.lid2, "bob2")
        mine = self.svc.create(ALICE, self.lid, "alice")
        res = maintenance.delete_user_data(self.repo, self.env.storage, BOB)
        self.assertEqual(store.conn().execute("SELECT COUNT(*) FROM user_notes WHERE user_id=?", (BOB,)).fetchone()[0], 0)
        self.assertEqual(self.svc.get(ALICE, mine["id"])["body"], "alice")
        self.assertEqual(store.conn().execute("SELECT COUNT(*) FROM user_note_links WHERE note_id NOT IN (SELECT id FROM user_notes)").fetchone()[0], 0)
        self.assertIsNotNone(res)


class TestNotesHttp(unittest.TestCase):
    """Over REAL HTTP against server.dev_server (same handlers the FastAPI routes call)."""

    @classmethod
    def setUpClass(cls):
        cls._env = os.environ.get("NOTIV_AUTH_SECRET")
        os.environ["NOTIV_AUTH_SECRET"] = SECRET
        cls.tmp = tempfile.mkdtemp()
        cls.env = Env(cls.tmp)
        cls._old = (config._repo, config._storage)
        config.configure(cls.env.repo, cls.env.storage)
        cls.ws = cls.env.repo.create_workspace(ALICE, "S"); cls.env.repo.add_member(cls.ws, BOB)
        cls.lid = cls.env.lectures.create(ALICE, "L", workspace_id=cls.ws)
        cls.ev = cls.env.repo.replace_evidence(cls.lid, [{"source_type": "transcript", "source_id": 1, "text": "Some lecture line.", "start": 1.0, "end": 2.0}])
        from server.dev_server import make_server
        cls.srv = make_server(0)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.tok = {u: create_token(u, SECRET, 3600) for u in (ALICE, BOB, EVE)}

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        config._repo, config._storage = cls._old
        shutil.rmtree(cls.tmp, ignore_errors=True)
        os.environ.pop("NOTIV_AUTH_SECRET", None) if cls._env is None else os.environ.__setitem__("NOTIV_AUTH_SECRET", cls._env)

    def call(self, method, path, body=None, user=ALICE, raw=None):
        h = {"Content-Type": "application/json"}
        if user: h["Authorization"] = "Bearer " + self.tok[user]
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + path, data=data, method=method, headers=h)) as r:
                return r.status, json.loads(r.read() or b"null"), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null"), dict(e.headers)

    def test_full_lifecycle_over_http(self):
        code, n, _ = self.call("POST", f"/lectures/{self.lid}/notes", {"body": "hello", "evidence_ids": self.ev, "t": 1.5})
        self.assertEqual(code, 200, n)
        nid = n["id"]
        self.assertEqual(self.call("GET", f"/notes/{nid}")[1]["links"][0]["evidence_id"], self.ev[0])
        self.assertIn(("hello", nid), [(x["body"], x["id"]) for x in self.call("GET", f"/lectures/{self.lid}/notes")[1]["notes"]])
        self.assertIn(nid, [x["id"] for x in self.call("GET", f"/lectures/{self.lid}/notes?evidence_id={self.ev[0]}")[1]["notes"]])
        code, u, _ = self.call("PATCH", f"/notes/{nid}", {"body": "changed", "evidence_ids": []})
        self.assertEqual((code, u["body"], u["links"]), (200, "changed", []))
        self.assertEqual(self.call("DELETE", f"/notes/{nid}")[1], {"deleted": True})
        self.assertEqual(self.call("GET", f"/notes/{nid}")[0], 404)

    def test_isolation_and_auth(self):
        nid = self.call("POST", f"/lectures/{self.lid}/notes", {"body": "private"})[1]["id"]
        self.assertEqual(self.call("GET", f"/lectures/{self.lid}/notes", user=BOB)[1]["notes"], [])
        for m, p, b in (("GET", f"/notes/{nid}", None), ("PATCH", f"/notes/{nid}", {"body": "x"}), ("DELETE", f"/notes/{nid}", None)):
            code, body, _ = self.call(m, p, b, user=BOB)
            self.assertEqual((code, body), (404, {"detail": "not found"}))
        self.assertEqual(self.call("GET", f"/lectures/{self.lid}/notes", user=EVE)[0], 404)
        self.assertEqual(self.call("GET", f"/lectures/{self.lid}/notes", user=None)[0], 401)
        self.assertEqual(self.call("POST", f"/lectures/{self.lid}/notes", {"body": "x"}, user=None)[0], 401)
        self.assertEqual(self.call("GET", f"/notes/{nid}")[1]["body"], "private")

    def test_bad_input_is_4xx_never_500(self):
        self.assertEqual(self.call("POST", f"/lectures/{self.lid}/notes", {})[0], 422)                       # missing body
        self.assertEqual(self.call("POST", f"/lectures/{self.lid}/notes", {"body": "x", "nope": 1})[0], 422)  # unknown field
        self.assertEqual(self.call("POST", f"/lectures/{self.lid}/notes", {"body": 5})[0], 422)               # wrong type
        self.assertEqual(self.call("POST", f"/lectures/{self.lid}/notes", {"body": "x", "evidence_ids": ["a"]})[0], 422)
        self.assertEqual(self.call("POST", f"/lectures/{self.lid}/notes", {"body": ""})[0], 400)
        self.assertEqual(self.call("POST", f"/lectures/{self.lid}/notes", {"body": "x", "evidence_ids": [999999]})[0], 400)
        self.assertEqual(self.call("POST", f"/lectures/{self.lid}/notes", raw=b"{not json")[0], 422)
        mine = self.call("POST", f"/lectures/{self.lid}/notes", {"body": "m"})[1]["id"]
        # a malformed patch gives the SAME answer for my note, someone else's note and a missing note (no existence oracle)
        self.assertEqual({self.call("PATCH", f"/notes/{i}", {"bogus": 1}, user=BOB)[0] for i in (mine, 999999)}, {400})
        nid = self.call("POST", f"/lectures/{self.lid}/notes", {"body": "x"})[1]["id"]
        self.assertEqual(self.call("PATCH", f"/notes/{nid}", {"bogus": 1})[0], 400)
        for path in (f"/notes/{HUGE}", f"/notes/0", f"/lectures/{HUGE}/notes", f"/lectures/{self.lid}/notes?evidence_id={HUGE}"):
            code = self.call("GET", path)[0]
            self.assertIn(code, (400, 404), path)

    def test_routes_exist_in_dev_server_and_fastapi_module(self):
        from server.dev_server import STUDY_ROUTES
        have = {(m, rx.pattern) for m, rx, _, _ in STUDY_ROUTES}
        for want in (("GET", r"^/lectures/(\d+)/notes$"), ("POST", r"^/lectures/(\d+)/notes$"), ("GET", r"^/notes/(\d+)$"),
                     ("PATCH", r"^/notes/(\d+)$"), ("DELETE", r"^/notes/(\d+)$")):
            self.assertIn(want, have)


try:
    import fastapi  # noqa: F401
    import httpx  # noqa: F401
    HAVE_FASTAPI = True
except ImportError:
    HAVE_FASTAPI = False


@unittest.skipUnless(HAVE_FASTAPI, "fastapi/httpx not installed (notes FastAPI routes UNVERIFIED)")
class TestNotesFastApi(unittest.TestCase):
    def test_notes_routes_mounted_and_crud(self):
        os.environ["NOTIV_AUTH_SECRET"] = SECRET
        from fastapi.testclient import TestClient
        from server.main import app
        from core.route_table import route_paths
        paths = route_paths(app)
        self.assertIn("/lectures/{lecture_id}/notes", paths); self.assertIn("/notes/{note_id}", paths)
        self.assertEqual(TestClient(app).get("/notes/1").status_code, 401)


if __name__ == "__main__":
    unittest.main()
