"""S3 integration test — SKIPPED unless NOTIV_TEST_S3_ENDPOINT + NOTIV_TEST_S3_BUCKET (+ AWS creds env) are set and boto3 is
installed. NOT run in the authoring sandbox. Against MinIO: docker compose -f docker-compose.sprint5.yml up -d minio, create
a private bucket `notiv-test`, export NOTIV_TEST_S3_ENDPOINT=http://localhost:59000 NOTIV_TEST_S3_BUCKET=notiv-test
AWS_ACCESS_KEY_ID=notiv AWS_SECRET_ACCESS_KEY=notivnotiv."""
import os
import unittest
import urllib.error
import urllib.request

EP, BUCKET = os.getenv("NOTIV_TEST_S3_ENDPOINT", ""), os.getenv("NOTIV_TEST_S3_BUCKET", "")
try:
    import boto3  # noqa: F401
    HAVE = True
except ImportError:
    HAVE = False


@unittest.skipUnless(EP and BUCKET and HAVE, "NOTIV_TEST_S3_ENDPOINT/NOTIV_TEST_S3_BUCKET and boto3 required (S3 UNVERIFIED without them)")
class TestRealS3(unittest.TestCase):
    def setUp(self):
        from core.lecture.storage import S3Storage
        self.s = S3Storage(BUCKET, "it-" + str(os.getpid()), EP)

    def tearDown(self):
        self.s.delete_prefix("lectures")

    def test_roundtrip_signed_url_and_delete(self):
        self.s.put_bytes("lectures/1/a.png", b"\x89PNG-data")
        self.assertTrue(self.s.exists("lectures/1/a.png")); self.assertEqual(self.s.read_bytes("lectures/1/a.png"), b"\x89PNG-data")
        url = self.s.signed_url("lectures/1/a.png", 60)
        with urllib.request.urlopen(url) as r:
            self.assertEqual(r.read(), b"\x89PNG-data"); self.assertEqual(r.headers["Content-Type"], "image/png")
        with self.assertRaises(urllib.error.HTTPError):                                    # private: no URL without a signature
            urllib.request.urlopen(url.split("?")[0])
        self.assertEqual([k for k, _ in self.s.iter_objects("lectures")], ["lectures/1/a.png"])
        self.assertEqual(self.s.delete_prefix("lectures/1"), 1); self.assertFalse(self.s.exists("lectures/1/a.png"))

    def test_private_bucket_is_proven_private_and_probe_object_is_cleaned_up(self):
        r = self.s.check_private()
        self.assertIs(r["private"], True, r)                       # the compose bucket has no public policy/ACL
        self.assertEqual([k for k, _ in self.s.iter_objects(".notiv-privacy-probe")], [])

    def test_readiness_storage_private_check_passes_in_production(self):
        from core.lecture import health
        health._priv_cache.update(at=0.0, val=None, key=None)
        os.environ["NOTIV_ENV"] = "production"
        try:
            self.assertTrue(health._storage_private(self.s)["ok"])
        finally:
            os.environ.pop("NOTIV_ENV", None)


if __name__ == "__main__":
    unittest.main()
