"""Regression tests for authenticated, workspace-scoped library listing APIs."""
import os
import tempfile
import unittest
from fastapi.testclient import TestClient

from core.lecture import config
from core.lecture.testkit import Env
from server.auth_tokens import create_token
from server.main import app

SECRET = "library-test-secret-which-is-long-enough-123456"


class TestLibraryApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_secret = os.environ.get("NOTIV_AUTH_SECRET")
        self.old_worker = os.environ.get("NOTIV_INPROCESS_WORKER")
        os.environ["NOTIV_AUTH_SECRET"] = SECRET
        self.env = Env(self.tmp.name)
        config.configure(self.env.repo, self.env.storage)
        self.client = TestClient(app)
        self.headers = {"Authorization": "Bearer " + create_token(11, SECRET, 3600)}
        self.other_headers = {"Authorization": "Bearer " + create_token(22, SECRET, 3600)}

    def tearDown(self):
        if self.old_secret is None:
            os.environ.pop("NOTIV_AUTH_SECRET", None)
        else:
            os.environ["NOTIV_AUTH_SECRET"] = self.old_secret
        if self.old_worker is None:
            os.environ.pop("NOTIV_INPROCESS_WORKER", None)
        else:
            os.environ["NOTIV_INPROCESS_WORKER"] = self.old_worker
        self.tmp.cleanup()

    def test_requires_authentication(self):
        self.assertEqual(self.client.get("/courses").status_code, 401)
        self.assertEqual(self.client.get("/lectures").status_code, 401)

    def test_lists_only_workspaces_user_can_access(self):
        cid = self.env.lectures.create_course(11, "Algorithms")
        lid = self.env.lectures.create(11, "Binary trees", course_id=cid)
        hidden_cid = self.env.lectures.create_course(22, "Private course")
        self.env.lectures.create(22, "Private lecture", course_id=hidden_cid)

        courses = self.client.get("/courses", headers=self.headers).json()["items"]
        lectures = self.client.get("/lectures", headers=self.headers).json()["items"]
        self.assertEqual([c["name"] for c in courses], ["Algorithms"])
        self.assertEqual([l["title"] for l in lectures], ["Binary trees"])
        self.assertEqual(lectures[0]["id"], lid)
        self.assertEqual(self.client.get(f"/lectures?course_id={hidden_cid}", headers=self.headers).status_code, 404)

    def test_shared_workspace_visibility_is_membership_scoped(self):
        ws = self.env.repo.create_workspace(11, "Shared")
        self.env.repo.add_member(ws, 22)
        cid = self.env.lectures.create_course(11, "Shared course", ws)
        lid = self.env.lectures.create(11, "Shared lecture", course_id=cid)
        course_response = self.client.get("/courses", headers=self.other_headers)
        lecture_response = self.client.get("/lectures", headers=self.other_headers)
        self.assertEqual(course_response.status_code, 200)
        self.assertIn(cid, [c["id"] for c in course_response.json()["items"]])
        self.assertIn(lid, [l["id"] for l in lecture_response.json()["items"]])

    def test_create_course_lecture_upload_and_queue_processing(self):
        os.environ["NOTIV_INPROCESS_WORKER"] = "0"
        course = self.client.post("/courses", headers=self.headers, json={"name": "Computer Science"})
        self.assertEqual(course.status_code, 201, course.text)
        cid = course.json()["course_id"]
        lecture = self.client.post("/lectures", headers=self.headers, json={"title": "Intro to graphs", "course_id": cid})
        self.assertEqual(lecture.status_code, 201, lecture.text)
        lid = lecture.json()["lecture_id"]
        pdf = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
               b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
               b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 100 100]/Resources<<>>/Contents 4 0 R>>endobj\n"
               b"4 0 obj<</Length 0>>stream\n\nendstream endobj\ntrailer<</Root 1 0 R>>\n%%EOF")
        upload = self.client.post(f"/lectures/{lid}/assets", headers=self.headers,
                                  files={"file": ("intro.pdf", pdf, "application/pdf")})
        self.assertEqual(upload.status_code, 201, upload.text)
        self.assertEqual(upload.json()["type"], "pdf")
        queued = self.client.post(f"/lectures/{lid}/process", headers=self.headers)
        self.assertEqual(queued.status_code, 202, queued.text)
        self.assertIn("job_id", queued.json())
        listed = self.client.get("/lectures", headers=self.headers).json()["items"]
        self.assertEqual(listed[0]["title"], "Intro to graphs")
        self.assertEqual(self.client.delete(f"/lectures/{lid}", headers=self.headers).status_code, 204)

    def test_pagination_bounds(self):
        self.assertEqual(self.client.get("/lectures?limit=201", headers=self.headers).status_code, 422)
        self.assertEqual(self.client.get("/courses?offset=-1", headers=self.headers).status_code, 422)

    def test_library_and_hero_assets_are_served(self):
        page = self.client.get("/app")
        self.assertEqual(page.status_code, 200)
        self.assertIn("/static/ui/library.js", page.text)
        asset = self.client.get("/static/assets/notiv-hero.jpg")
        self.assertEqual(asset.status_code, 200)
        self.assertTrue(asset.content.startswith(b"\xff\xd8\xff"))
        viewer = self.client.get("/app/lectures/123")
        self.assertEqual(viewer.status_code, 200)
        self.assertIn("/static/ui/lecture.js", viewer.text)
        import hashlib   # the landing hero must stay byte-identical to the supplied photograph (no re-encode, no filter)
        self.assertEqual(hashlib.sha256(asset.content).hexdigest(), "7ed3a0e023612b3ae4e27b2f0703ec1e07fbd23fc902fa4805f319d80e540e63")


if __name__ == "__main__":
    unittest.main()
