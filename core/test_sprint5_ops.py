"""Sprint 5 service-level tests (SQLite + local storage; S3 against an in-memory FAKE client — not a real bucket)."""
import asyncio
import contextlib
import os
import shutil
import tempfile
import threading
import time
import unittest
import uuid
import unittest.mock

from core.lecture import (authz, idempotency, jobs, maintenance, obs, ops_store, quotas, ratelimit, store)
from core.lecture.errors import ConflictError, NotFoundError, PermanentError, QuotaError, RateLimitError, ValidationError
from core.lecture.storage import LocalStorage, S3Storage, StorageError, clamp_ttl
from core.lecture.testkit import Env
from server.auth_tokens import AuthError, create_token, verify_token

SECRET = "k" * 40


@contextlib.contextmanager
def env(**kv):
    old = {k: os.environ.get(k) for k in kv}
    os.environ.update({k: str(v) for k, v in kv.items()}); ratelimit.reset()
    try:
        yield
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        ratelimit.reset()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = Env(self.tmp); self.repo = self.env.repo
        self.lid = self.env.lectures.create(1, "L")
        self.ws = self.repo.get_lecture(self.lid)["workspace_id"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def svc(self, *fns, **kw):
        return jobs.JobService(self.repo, [jobs.Stage(f"s{i}", f) for i, f in enumerate(fns)], lambda: None, backoff_base=0, **kw)


class TestJobs(Base):
    def test_enqueue_is_idempotent_while_active(self):
        js = self.svc(lambda c: {})
        a, b = js.enqueue(self.lid), js.enqueue(self.lid)
        self.assertEqual(a, b)
        self.assertEqual(len(self.repo.list_jobs(self.lid)), 1)

    def test_concurrent_enqueue_creates_one_job(self):
        js, ids = self.svc(lambda c: {}), []
        ts = [threading.Thread(target=lambda: ids.append(js.enqueue(self.lid))) for _ in range(8)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(len(set(ids)), 1); self.assertEqual(len(self.repo.list_jobs(self.lid)), 1)

    def test_new_job_after_completion(self):
        js = self.svc(lambda c: {})
        a = js.enqueue(self.lid); js.run_job(a)
        self.assertNotEqual(js.enqueue(self.lid), a)

    def test_db_enforces_single_active_job(self):
        self.repo.create_job(self.lid, self.ws, "process_lecture")
        with self.assertRaises(store.INTEGRITY_ERRORS):
            self.repo.create_job(self.lid, self.ws, "process_lecture")

    def test_error_text_is_redacted_but_class_kept(self):
        def boom(c): raise RuntimeError("cannot open /var/data/secret/file.mp4 using Bearer abcdefghijklmnop")
        js = self.svc(boom); jid = js.enqueue(self.lid, max_attempts=1); res = js.run_job(jid)
        self.assertEqual(res.status, "failed")
        j = self.repo.get_job(jid)
        for txt in (j["error"], self.repo.get_lecture(self.lid)["error"], self.repo.get_stage(jid, "s0")["error"]):
            self.assertNotIn("/var/data", txt); self.assertNotIn("abcdefghijklmnop", txt)
        self.assertEqual(j["error_class"], "RuntimeError")

    def test_retry_then_permanent(self):
        calls = []
        def flaky(c):
            calls.append(1)
            if len(calls) == 1: raise RuntimeError("transient")
            return {}
        js = self.svc(flaky); jid = js.enqueue(self.lid, max_attempts=3)
        self.assertEqual(js.run_job(jid).status, "retrying"); self.assertEqual(js.run_job(jid).status, "completed")
        def perm(c): raise PermanentError("bad")
        l2 = self.env.lectures.create(1, "L2"); js2 = self.svc(perm); j2 = js2.enqueue(l2, max_attempts=5)
        self.assertEqual(js2.run_job(j2).status, "failed")

    def test_lease_fencing_stops_a_worker_that_lost_the_job(self):
        js = self.svc(lambda c: {}, lambda c: {}); jid = js.enqueue(self.lid)
        job = self.repo.claim_next_job(time.time(), worker_id="A")
        self.assertTrue(self.repo.job_owned_by(jid, "A")); self.assertFalse(self.repo.job_owned_by(jid, "B"))
        self.repo.recover_stale_jobs(time.time() + 10)                      # A's heartbeat is stale -> back to retrying
        self.repo.claim_next_job(time.time(), worker_id="B")                # B takes over
        self.assertEqual(js._run_attempt(job).status, jobs.LOST)            # A wakes up: must NOT write anything
        self.assertEqual(self.repo.get_job(jid)["worker_id"], "B")
        self.assertIsNone(self.repo.get_stage(jid, "s0"))

    # ── regression: job-finalization lease race (check-then-write) ────────────────────────────────────
    def _takeover_during(self, jid):
        """Simulates the lease being lost at the worst instant: after A's last ownership check, B recovers+claims the job."""
        self.repo.update_job(jid, heartbeat_at=1)
        self.repo.recover_stale_jobs(time.time() - 1)
        self.assertIsNotNone(self.repo.claim_next_job(time.time(), worker_id="B"))

    def test_finalize_race_completion_cannot_overwrite_new_owner(self):
        def last_stage(c):
            self._takeover_during(c.job["id"]); return {}
        js = self.svc(last_stage, worker_id="A"); jid = js.enqueue(self.lid)
        # the OLD code's separate `_owned()` check passed (stale positive); only the fenced write can stop the overwrite
        with unittest.mock.patch.object(self.repo, "job_owned_by", return_value=True):
            res = js.run_job(jid)
        self.assertEqual(res.status, jobs.LOST)
        j = self.repo.get_job(jid)
        self.assertEqual((j["status"], j["worker_id"]), ("running", "B"))             # B's lease untouched
        self.assertNotEqual(self.repo.get_lecture(self.lid)["status"], "done")        # A did not declare the lecture done
        self.assertNotEqual((self.repo.get_stage(jid, "s0") or {}).get("status"), "completed")

    def test_finalize_race_failure_cannot_overwrite_new_owner(self):
        def boom(c):
            self._takeover_during(c.job["id"]); raise RuntimeError("late failure")
        js = self.svc(boom, worker_id="A"); jid = js.enqueue(self.lid)
        with unittest.mock.patch.object(self.repo, "job_owned_by", return_value=True):
            res = js.run_job(jid)
        self.assertEqual(res.status, jobs.LOST)
        j = self.repo.get_job(jid)
        self.assertEqual((j["status"], j["worker_id"], j["error"]), ("running", "B", None))
        self.assertNotEqual(self.repo.get_lecture(self.lid)["status"], "failed")

    def test_finalize_owned_is_atomic_and_fenced(self):
        js = self.svc(lambda c: {}); jid = js.enqueue(self.lid)
        self.repo.claim_next_job(time.time(), worker_id="A")
        self.assertFalse(self.repo.finalize_owned(jid, "B", {"status": "completed"}, {"status": "done"}, ("s0", "completed", None, {})))
        self.assertEqual(self.repo.get_job(jid)["status"], "running"); self.assertIsNone(self.repo.get_stage(jid, "s0"))
        self.assertTrue(self.repo.finalize_owned(jid, "A", {"status": "completed", "progress": 100}, {"status": "done", "stage": "Done"},
                                                 ("s0", "completed", None, {"k": 1})))
        self.assertEqual(self.repo.get_job(jid)["status"], "completed"); self.assertEqual(self.repo.get_lecture(self.lid)["status"], "done")
        self.assertFalse(self.repo.finalize_owned(jid, "A", {"status": "failed"}))        # no longer running: nothing more may be written
        self.assertEqual(self.repo.get_job(jid)["status"], "completed")

    def test_poison_pill_fails_after_max_attempts(self):
        jid = self.svc(lambda c: {}).enqueue(self.lid, max_attempts=2)
        for _ in range(2):
            self.repo.claim_next_job(time.time(), worker_id="A"); self.repo.update_job(jid, heartbeat_at=1)
            self.repo.recover_stale_jobs(time.time() - 1)
        j = self.repo.get_job(jid)
        self.assertEqual((j["status"], j["error_class"]), ("failed", "WorkerLost"))
        self.assertEqual(self.repo.get_lecture(self.lid)["status"], "failed")

    def test_heartbeat_keeps_lease_during_long_stage(self):
        def slow(c): time.sleep(0.5); return {}
        js = self.svc(slow, heartbeat_interval=0.1); jid = js.enqueue(self.lid)
        self.repo.update_job(jid, heartbeat_at=1)
        js.run_job(jid)
        self.assertEqual(self.repo.get_job(jid)["status"], "completed")

    def test_cancel_running_is_respected(self):
        js = self.svc(lambda c: self.repo.update_job(c.job["id"], status="cancelled") or {}, lambda c: self.fail("ran after cancel"))
        jid = js.enqueue(self.lid); self.assertEqual(js.run_job(jid).status, "cancelled")

    def test_retry_blocked_when_another_job_is_active(self):
        js = self.svc(lambda c: (_ for _ in ()).throw(PermanentError("x"))); a = js.enqueue(self.lid); js.run_job(a)   # a failed
        b = js.enqueue(self.lid)                                                                                     # new active job
        self.assertFalse(js.retry(a)); self.assertNotEqual(a, b)


class TestLimitsQuotasIdempotency(Base):
    def test_ratelimit_window_and_isolation(self):
        with env(NOTIV_RL_READ="2/60"):
            ratelimit.check("read", 1, now=1000); ratelimit.check("read", 1, now=1001)
            with self.assertRaises(RateLimitError) as cm: ratelimit.check("read", 1, now=1002)
            self.assertTrue(1 <= cm.exception.retry_after <= 60)
            ratelimit.check("read", 2, now=1002)                  # other subject unaffected
            ratelimit.check("read", 1, now=1061)                  # next window

    def test_ratelimit_db_backend_is_shared_and_healthy_state_is_global(self):
        ratelimit.reset_state()
        with env(NOTIV_RL_READ="1/60", NOTIV_RATELIMIT_BACKEND="db"):
            ratelimit.check("read", 9, now=5000)
            with self.assertRaises(RateLimitError): ratelimit.check("read", 9, now=5001)
            st = ratelimit.status()
            self.assertEqual((st["backend"], st["scope"], st["degraded"]), ("db", "global", False))

    def test_db_outage_default_is_explicit_per_process_degradation_not_silent_unlimited(self):
        from core.lecture import health
        ratelimit.reset_state(); obs.reset_metrics()
        with env(NOTIV_RL_READ="2/60", NOTIV_RATELIMIT_BACKEND="db"):
            with unittest.mock.patch.object(ops_store, "rl_hit", side_effect=RuntimeError("db down")):
                ratelimit.check("read", 1, now=1); ratelimit.check("read", 1, now=2)
                with self.assertRaises(RateLimitError):                           # STILL limited (per process), not waved through
                    ratelimit.check("read", 1, now=3)
                st = ratelimit.status()
                self.assertTrue(st["degraded"]); self.assertIn("DEGRADED", st["scope"]); self.assertEqual(st["on_error"], "local")
                self.assertIn("notiv_ratelimit_backend_errors_total", obs.render_prometheus())
                rl = health.readiness()["checks"]["rate_limit"]                    # surfaced to operators, not hidden
                self.assertFalse(rl["ok"]); self.assertIn("NOT in effect", rl["detail"])
            # store is back: counted globally again. Unique subject: the 'db' backend writes to the process-default DB, which can
            # outlive a test run, so a fixed subject id would collide with rows left by earlier runs.
            ratelimit.check("read", "outage-recovery-" + uuid.uuid4().hex, now=100)
            self.assertFalse(ratelimit.status()["degraded"])

    def test_db_outage_deny_mode_fails_closed_with_503(self):
        from core.lecture.errors import LimiterUnavailable
        ratelimit.reset_state()
        with env(NOTIV_RL_READ="100/60", NOTIV_RATELIMIT_BACKEND="db", NOTIV_RATELIMIT_ON_ERROR="deny"):
            with unittest.mock.patch.object(ops_store, "rl_hit", side_effect=RuntimeError("db down")):
                with self.assertRaises(LimiterUnavailable) as cm: ratelimit.check("read", 1, now=1)
                self.assertEqual(cm.exception.http_status, 503); self.assertTrue(cm.exception.retry_after >= 1)

    def test_memory_backend_never_reports_degraded(self):
        ratelimit.reset_state()
        with env(NOTIV_RATELIMIT_BACKEND="memory"):
            self.assertEqual((ratelimit.status()["scope"], ratelimit.status()["degraded"]), ("per-process", False))

    def test_quotas(self):
        with env(NOTIV_QUOTA_LECTURES="1"):
            with self.assertRaises(QuotaError): quotas.check_create_lecture(1)
            quotas.check_create_lecture(2)
        with env(NOTIV_QUOTA_ACTIVE_JOBS="1"):
            self.svc(lambda c: {}).enqueue(self.lid)
            with self.assertRaises(QuotaError): quotas.check_process(1)
        with env(NOTIV_QUOTA_STORAGE_MB="1"):
            with self.assertRaises(QuotaError): quotas.check_upload(1, self.lid, 2 * 1024 * 1024)
        with env(NOTIV_QUOTA_LECTURES="0"):
            quotas.check_create_lecture(1)                         # <=0 disables

    def test_upload_quota_enforced_in_asset_service(self):
        src = os.path.join(self.tmp, "a.pdf")
        from reportlab.pdfgen import canvas
        cv = canvas.Canvas(src); cv.drawString(50, 50, "quota"); cv.showPage(); cv.save()
        with env(NOTIV_QUOTA_ASSETS_PER_LECTURE="1"):
            self.env.assets.add_from_file(1, self.lid, src, "a.pdf")
            with self.assertRaises(QuotaError): self.env.assets.add_from_file(1, self.lid, src, "b.pdf")
        self.assertEqual([a["action"] for a in ops_store.audit_list(1)][:1], ["asset.upload"])

    def test_idempotency_replay_conflict_and_release(self):
        n = []
        def work(): n.append(1); return 201, {"id": len(n)}
        s1 = idempotency.run(1, "lec.create", "key-12345678", {"t": "a"}, work)
        s2 = idempotency.run(1, "lec.create", "key-12345678", {"t": "a"}, work)
        self.assertEqual((s1, s2), ((201, {"id": 1}, False), (201, {"id": 1}, True))); self.assertEqual(len(n), 1)
        with self.assertRaises(ConflictError) as cm: idempotency.run(1, "lec.create", "key-12345678", {"t": "DIFFERENT"}, work)
        self.assertEqual(cm.exception.code, "idempotency_key_reused")
        self.assertFalse(idempotency.run(2, "lec.create", "key-12345678", {"t": "a"}, work)[2])      # per-user keys
        def fail(): raise RuntimeError("x")
        with self.assertRaises(RuntimeError): idempotency.run(1, "s", "key-fail-0001", {}, fail)
        self.assertEqual(idempotency.run(1, "s", "key-fail-0001", {}, work)[0], 201)                   # released after failure
        with self.assertRaises(ValidationError): idempotency.run(1, "s", "short", {}, work)


class TestDeletionAndPrivacy(Base):
    def _put(self, lid, name="x.bin", age=0):
        key = f"lectures/{lid}/assets/{name}"; self.env.storage.put_bytes(key, b"data")
        if age:
            p = self.env.storage._path(key); os.utime(p, (time.time() - age, time.time() - age))
        return key

    def test_delete_lecture_removes_objects_and_outbox_is_drained(self):
        key = self._put(self.lid)
        self.env.assets.delete_lecture(1, self.lid)
        self.assertFalse(self.env.storage.exists(key)); self.assertEqual(ops_store.outbox_pending(), [])
        self.assertIsNone(self.repo.get_lecture(self.lid))

    def test_failed_object_delete_stays_queued_and_is_retried(self):
        key = self._put(self.lid)
        with unittest.mock.patch.object(LocalStorage, "delete_prefix", side_effect=OSError("disk /var/x error")):
            self.env.assets.delete_lecture(1, self.lid)
        rows = ops_store.outbox_pending(); self.assertEqual(len(rows), 1)
        self.assertNotIn("/var/x", rows[0]["last_error"]); self.assertTrue(self.env.storage.exists(key))
        self.assertEqual(maintenance.process_outbox(self.env.storage), {"done": 1, "failed": 0})
        self.assertFalse(self.env.storage.exists(key))

    def test_orphan_sweep_respects_grace_and_known_lectures(self):
        live = self._put(self.lid, age=10 ** 6); orphan_old = self._put(999, "o.bin", age=10 ** 6); orphan_new = self._put(998, "n.bin")
        self.assertEqual(maintenance.sweep_orphans(self.env.storage, dry_run=True), [orphan_old])
        maintenance.sweep_orphans(self.env.storage)
        self.assertTrue(self.env.storage.exists(live)); self.assertTrue(self.env.storage.exists(orphan_new))
        self.assertFalse(self.env.storage.exists(orphan_old))

    def test_stale_pending_assets_swept(self):
        aid = self.repo.add_asset(self.lid, self.ws, uploader_id=1, type="pdf", storage_key=f"lectures/{self.lid}/assets/s.pdf", status="pending")
        self.env.storage.put_bytes(f"lectures/{self.lid}/assets/s.pdf", b"x")
        self.assertEqual(maintenance.sweep_stale_assets(self.repo, self.env.storage, now=time.time() + 90000), 1)
        self.assertIsNone(self.repo.get_asset(aid))

    def test_delete_user_data_is_scoped_to_that_user(self):
        other = self.env.lectures.create(2, "bob"); k2 = self._put(other); k1 = self._put(self.lid)
        self.repo.chat_conversation(self.lid, 1, create=True); self.repo.chat_conversation(other, 2, create=True)
        r = maintenance.delete_user_data(self.repo, self.env.storage, 1)
        self.assertEqual(r["lectures_deleted"], 1)
        self.assertFalse(self.env.storage.exists(k1)); self.assertTrue(self.env.storage.exists(k2))
        self.assertIsNone(self.repo.get_lecture(self.lid)); self.assertIsNotNone(self.repo.get_lecture(other))
        self.assertIsNone(self.repo.chat_conversation(self.lid, 1)); self.assertIsNotNone(self.repo.chat_conversation(other, 2))


class TestAuthzIds(Base):
    def test_invalid_ids_are_plain_not_found(self):
        for bad in (0, -1, 2 ** 63, 10 ** 30, None, "1", True, 1.5):
            for fn in (authz.get_lecture, authz.get_course, authz.get_asset, authz.get_job, authz.get_evidence):
                with self.assertRaises(NotFoundError, msg=f"{fn.__name__}({bad!r})"): fn(self.repo, 1, bad)
        self.assertEqual(authz.accessible_lecture_ids(self.repo, 1, [self.lid, 0, 10 ** 30, "x"]), [self.lid])

    def test_cross_workspace_isolation(self):
        with self.assertRaises(NotFoundError): authz.get_lecture(self.repo, 2, self.lid)


class TestTokens(unittest.TestCase):
    def test_rotation_nbf_iss_aud_ttl_size_uid(self):
        old, new = "o" * 40, "n" * 40
        t = create_token(7, old, 600, now=1000)
        with self.assertRaises(AuthError): verify_token(t, new, now=1100)
        self.assertEqual(verify_token(t, new, now=1100, previous_secrets=[old]), 7)
        self.assertEqual(verify_token(create_token(7, new, 60, now=1000, issuer="i", audience="a"), new, 1010, issuer="i", audience="a"), 7)
        with self.assertRaises(AuthError): verify_token(create_token(7, new, 60, now=1000, issuer="x"), new, 1010, issuer="i")
        with self.assertRaises(AuthError): verify_token(create_token(7, new, 60, now=1000), new, 1010, audience="a")
        with self.assertRaises(AuthError): verify_token(create_token(7, new, 7200, now=1000), new, 1010, max_ttl=3600)
        with self.assertRaises(AuthError): verify_token("a" * 5000, new)
        with self.assertRaises(AuthError): verify_token(create_token(2 ** 70, new, 60, now=1000), new, 1010)
        with self.assertRaises(AuthError): verify_token(create_token(0, new, 60, now=1000), new, 1010)
        self.assertEqual(verify_token(create_token(1, new, 60, now=1000), new, now=1059), 1)       # exact expiry preserved
        with self.assertRaises(AuthError): verify_token(create_token(1, new, 60, now=1000), new, now=1060)

    def test_nbf(self):
        import base64, hashlib, hmac, json
        b = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
        si = f"{b({'alg': 'HS256'})}.{b({'sub': '1', 'exp': 2000, 'nbf': 1500})}"
        tok = si + "." + base64.urlsafe_b64encode(hmac.new(SECRET.encode(), si.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
        with self.assertRaises(AuthError): verify_token(tok, SECRET, now=1000)
        self.assertEqual(verify_token(tok, SECRET, now=1600), 1)


class TestStorage(Base):
    def test_ttl_clamp_and_signature_hardening(self):
        self.assertEqual(clamp_ttl(10 ** 9), 3600); self.assertEqual(clamp_ttl(-5), 1); self.assertEqual(clamp_ttl("x"), 900)
        st = self.env.storage; st.put_bytes("lectures/1/a.png", b"x")
        url = st.signed_url("lectures/1/a.png", 10 ** 9); exp = int(url.split("exp=")[1].split("&")[0]); sig = url.split("sig=")[1]
        self.assertTrue(st.verify_signed("lectures/1/a.png", exp, sig))
        self.assertFalse(st.verify_signed("lectures/1/b.png", exp, sig))
        for bad in ("abc", None, 10 ** 30, -1):
            self.assertFalse(st.verify_signed("lectures/1/a.png", bad, sig))
        far = int(time.time()) + 10 ** 7; self.assertFalse(st.verify_signed("lectures/1/a.png", far, st._sig("lectures/1/a.png", far)))

    def test_symlink_escape_blocked(self):
        st = self.env.storage; st.put_bytes("lectures/1/a", b"x")
        outside = tempfile.mkdtemp(); open(os.path.join(outside, "s"), "w").write("secret")
        os.symlink(outside, os.path.join(st.root, "lectures", "1", "lnk"))
        with self.assertRaises(ValidationError): st.read_bytes("lectures/1/lnk/s")
        shutil.rmtree(outside)


class FakeS3:
    """In-memory stand-in for a boto3 S3 client. Verifies OUR call patterns only — NOT a real-S3 verification."""
    def __init__(self, fail_delete=False): self.objs, self.calls, self.fail_delete = {}, [], fail_delete
    def upload_file(self, src, b, k, ExtraArgs=None): self.objs[k] = open(src, "rb").read(); self.calls.append(("upload", k, ExtraArgs))
    def put_object(self, Bucket, Key, Body, **kw): self.objs[Key] = Body; self.calls.append(("put", Key, kw))
    def get_object(self, Bucket, Key):
        import io; return {"Body": io.BytesIO(self.objs[Key])}
    def head_object(self, Bucket, Key):
        if Key not in self.objs:
            e = Exception("nf"); e.response = {"Error": {"Code": "404"}}; raise e
    def head_bucket(self, Bucket): pass
    def delete_object(self, Bucket, Key): self.objs.pop(Key, None)
    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        keys = sorted(k for k in self.objs if k.startswith(Prefix) and (ContinuationToken is None or k > ContinuationToken))   # key-based, like S3
        page = keys[:2]
        r = {"Contents": [{"Key": k, "LastModified": __import__("datetime").datetime.now()} for k in page], "IsTruncated": len(keys) > 2}
        if r["IsTruncated"]: r["NextContinuationToken"] = page[-1]
        return r
    def delete_objects(self, Bucket, Delete):
        if self.fail_delete: return {"Errors": [{"Code": "AccessDenied", "Key": Delete["Objects"][0]["Key"]}]}
        for o in Delete["Objects"]: self.objs.pop(o["Key"], None)
        return {}
    def generate_presigned_url(self, op, Params, ExpiresIn): self.calls.append(("presign", Params, ExpiresIn)); return "https://x/y?X-Amz-Signature=s"
    def get_public_access_block(self, Bucket): return {"PublicAccessBlockConfiguration": dict(BlockPublicAcls=True, IgnorePublicAcls=True, BlockPublicPolicy=True, RestrictPublicBuckets=True)}


class TestS3Logic(unittest.TestCase):
    """Logic of S3Storage against a FAKE client. The real-bucket test is core/test_s3_integration.py (SKIPPED without an endpoint)."""
    def setUp(self): self.c = FakeS3(); self.s = S3Storage("b", "pfx", client=self.c, sse="AES256")

    def test_put_sets_content_type_sse_and_prefix(self):
        self.s.put_bytes("lectures/1/a.mp4", b"x")
        _, k, extra = self.c.calls[-1]
        self.assertEqual(k, "pfx/lectures/1/a.mp4"); self.assertEqual(extra["ContentType"], "video/mp4"); self.assertEqual(extra["ServerSideEncryption"], "AES256")

    def test_presign_clamped_with_safe_response_headers(self):
        self.s.signed_url("lectures/1/d.pdf", 10 ** 9)
        _, params, ttl = self.c.calls[-1]
        self.assertEqual(ttl, 3600); self.assertEqual(params["ResponseContentDisposition"], "attachment")

    def test_delete_prefix_paginates_and_raises_on_partial_failure(self):
        for i in range(5): self.s.put_bytes(f"lectures/1/{i}", b"x")
        self.s.put_bytes("lectures/10/keep", b"x")
        self.assertEqual(self.s.delete_prefix("lectures/1"), 5); self.assertTrue(self.s.exists("lectures/10/keep"))   # no prefix-collision
        bad = S3Storage("b", "", client=FakeS3(fail_delete=True)); bad.s3.objs["lectures/2/a"] = b"x"
        with self.assertRaises(StorageError): bad.delete_prefix("lectures/2")

    def test_iter_objects_strips_prefix_and_exists(self):
        self.s.put_bytes("lectures/1/a", b"x"); self.s.put_bytes("lectures/1/b", b"x"); self.s.put_bytes("lectures/1/c", b"x")
        self.assertEqual(sorted(k for k, _ in self.s.iter_objects("lectures")), ["lectures/1/a", "lectures/1/b", "lectures/1/c"])
        self.assertTrue(self.s.exists("lectures/1/a")); self.assertFalse(self.s.exists("lectures/1/zz"))

    def test_privacy_and_health(self):
        self.assertTrue(self.s.check_private()["private"]); self.assertTrue(self.s.health()["ok"])

    # ── private-bucket enforcement (item: production readiness must PROVE the bucket is private) ─────────
    def _store(self, **methods):
        c = FakeS3()
        for k, v in methods.items(): setattr(c, k, v)
        return S3Storage("b", "", client=c)

    @staticmethod
    def _raise(**kw):
        raise Exception("NotImplemented")

    def test_check_private_detects_public_acl_policy_and_partial_block(self):
        acl = lambda Bucket: {"Grants": [{"Grantee": {"URI": "http://acs.amazonaws.com/groups/global/AllUsers"}, "Permission": "READ"}]}
        self.assertIs(self._store(get_bucket_acl=acl).check_private()["private"], False)
        pol = lambda Bucket: {"PolicyStatus": {"IsPublic": True}}
        self.assertIs(self._store(get_bucket_policy_status=pol).check_private()["private"], False)

    def test_check_private_unknown_is_none_never_true(self):
        r = self._store(get_public_access_block=self._raise).check_private()          # nothing provable
        self.assertIsNone(r["private"])

    @staticmethod
    def _client_err(code):
        def f(**kw):
            e = Exception(code); e.response = {"Error": {"Code": code}}; raise e
        return f

    def _minio_like(self, probe, **over):
        """No public-access-block / policy-status API (like MinIO): privacy must come from policy + ACL + object probe."""
        m = dict(get_public_access_block=self._raise, get_bucket_policy_status=self._raise,
                 get_bucket_policy=self._client_err("NoSuchBucketPolicy"), get_bucket_acl=lambda Bucket: {"Grants": []})
        m.update(over)
        st = self._store(**m)
        return st, unittest.mock.patch.object(S3Storage, "_anonymous_get_known_object", return_value=probe)

    def test_proof_b_requires_policy_acl_AND_object_probe(self):
        st, p = self._minio_like("denied")
        with p: self.assertIs(st.check_private()["private"], True)                    # all three proven
        st, p = self._minio_like("denied", get_bucket_policy=self._raise)             # policy unreadable
        with p: self.assertIsNone(st.check_private()["private"])
        st, p = self._minio_like("denied", get_bucket_acl=self._raise)                # ACL unreadable
        with p: self.assertIsNone(st.check_private()["private"])
        st, p = self._minio_like(None)                                                # probe inconclusive
        with p: self.assertIsNone(st.check_private()["private"])
        st, p = self._minio_like("open")                                              # object readable anonymously
        with p: self.assertIs(st.check_private()["private"], False)

    def test_denied_listing_alone_is_never_proof(self):
        # Bucket can deny anonymous ListBucket yet allow anonymous GetObject. Nothing but a list denial -> NOT private.
        st = self._store(get_public_access_block=self._raise)
        with unittest.mock.patch.object(S3Storage, "_anonymous_get_known_object", return_value=None):
            self.assertIsNone(st.check_private()["private"])
        self.assertFalse(hasattr(S3Storage, "_anonymous_probe"))                      # the list-based probe is gone

    def test_public_policy_document_detection(self):
        import json
        pub = lambda **kw: json.dumps({"Statement": [dict(Effect="Allow", Action="s3:GetObject", Resource="*", **kw)]})
        self.assertTrue(S3Storage._policy_is_public(pub(Principal="*")))
        self.assertTrue(S3Storage._policy_is_public(pub(Principal={"AWS": "*"})))
        self.assertTrue(S3Storage._policy_is_public("not json"))                      # unparseable -> unsafe
        self.assertFalse(S3Storage._policy_is_public(pub(Principal="*", Condition={"StringEquals": {"aws:SourceVpce": "v"}})))
        self.assertFalse(S3Storage._policy_is_public(pub(Principal={"AWS": "arn:aws:iam::1:root"})))
        self.assertFalse(S3Storage._policy_is_public(json.dumps({"Statement": [{"Effect": "Deny", "Principal": "*"}]})))
        st = self._store(get_public_access_block=self._raise, get_bucket_policy_status=self._raise,
                         get_bucket_policy=lambda Bucket: {"Policy": pub(Principal="*")})
        self.assertIs(st.check_private()["private"], False)

    def test_object_probe_against_a_real_http_endpoint(self):
        """Exercises the REAL anonymous-GET code path against a local HTTP server standing in for the S3 endpoint
        (a local stdlib server is NOT S3; this proves the probe logic only)."""
        import http.server, threading, types
        class H(http.server.BaseHTTPRequestHandler):
            code = 403
            def do_GET(self): self.send_response(H.code); self.end_headers(); self.wfile.write(b"x")
            def log_message(self, *a): pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            c = FakeS3(); c.meta = types.SimpleNamespace(endpoint_url=f"http://127.0.0.1:{srv.server_address[1]}")
            st = S3Storage("b", "pfx", client=c)
            for code, want in ((403, "denied"), (401, "denied"), (200, "open"), (404, None)):
                H.code = code
                self.assertEqual(st._anonymous_get_known_object(), want, code)
            self.assertEqual(c.objs, {})                                              # probe object was cleaned up
            self.assertTrue(any(k == "put" for k, *_ in c.calls))
        finally:
            srv.shutdown(); srv.server_close()

    def test_readiness_fails_closed_in_production(self):
        from core.lecture import health
        def ready(storage, **env_):
            health._priv_cache.update(at=0.0, val=None, key=None)
            with unittest.mock.patch.dict(os.environ, env_):
                return health._storage_private(storage)
        prod = dict(NOTIV_ENV="production")
        self.assertTrue(ready(self._store(), **prod)["ok"])                                               # proven private
        self.assertFalse(ready(self._store(get_bucket_policy_status=lambda Bucket: {"PolicyStatus": {"IsPublic": True}}), **prod)["ok"])
        unk = self._store(get_public_access_block=self._raise)
        self.assertFalse(ready(unk, **prod)["ok"])                                                        # cannot prove -> NOT ready
        self.assertFalse(ready(unk, NOTIV_ENV="production", NOTIV_S3_SKIP_PRIVACY_CHECK="1")["ok"])       # override ignored in prod
        self.assertTrue(ready(unk, NOTIV_ENV="development")["ok"])                                        # dev: allowed, still reported
        self.assertTrue(ready(object(), **prod)["ok"])                                                    # local storage: n/a


class TestAsgiMiddleware(unittest.TestCase):
    """HardeningMiddleware is pure ASGI, so it can be exercised directly (no FastAPI needed)."""
    def run_app(self, inner, method="POST", path="/lectures/1/process", headers=(), body_chunks=(b"",)):
        from server.api.guards import HardeningMiddleware
        out = {"status": None, "headers": {}, "body": b""}
        chunks = list(body_chunks)
        async def receive():
            c = chunks.pop(0) if chunks else b""
            return {"type": "http.request", "body": c, "more_body": bool(chunks)}
        async def send(m):
            if m["type"] == "http.response.start":
                out["status"] = m["status"]; out["headers"] = {k.decode().lower(): v.decode() for k, v in m["headers"]}
            else: out["body"] += m.get("body", b"")
        scope = {"type": "http", "method": method, "path": path, "headers": [(k.lower().encode(), v.encode()) for k, v in headers]}
        asyncio.run(HardeningMiddleware(inner)(scope, receive, send)); return out

    @staticmethod
    async def ok_app(scope, receive, send):
        while (await receive()).get("more_body"): pass
        await send({"type": "http.response.start", "status": 200, "headers": [(b"server", b"uvicorn")]})
        await send({"type": "http.response.body", "body": b"{}"})

    def test_headers_request_id(self):
        r = self.run_app(self.ok_app, "GET", "/x", [("X-Request-ID", "trace-12345678")])
        self.assertEqual((r["status"], r["headers"]["x-request-id"], r["headers"]["x-content-type-options"]), (200, "trace-12345678", "nosniff"))
        self.assertNotIn("server", r["headers"])

    def test_content_length_limits(self):
        self.assertEqual(self.run_app(self.ok_app, headers=[("Content-Length", "x")])["status"], 400)
        self.assertEqual(self.run_app(self.ok_app, headers=[("Content-Length", str(10 ** 8))])["status"], 413)

    def test_streaming_overflow_without_content_length(self):
        big = [b"a" * 40000, b"a" * 40000]
        self.assertEqual(self.run_app(self.ok_app, body_chunks=big)["status"], 413)

    def test_unhandled_exception_is_generic(self):
        async def boom(scope, receive, send): raise RuntimeError("secret /home/u")
        r = self.run_app(boom, "GET", "/x")
        import json as _json
        self.assertEqual(r["status"], 500); self.assertEqual(_json.loads(r["body"]), {"detail": "internal server error"})
        self.assertRegex(r["headers"]["x-request-id"], r"^[A-Za-z0-9._-]{8,64}$")

    def test_policy_and_client_ip(self):
        from server.api.guards import client_ip, policy_for
        self.assertEqual([policy_for("POST", "/lectures/1/assets"), policy_for("POST", "/lectures/1/process"), policy_for("POST", "/lectures/1/chat"),
                          policy_for("GET", "/lectures/1/overview"), policy_for("DELETE", "/lectures/1")], ["upload", "process", "ask", "read", "write"])
        h = {"x-forwarded-for": "6.6.6.6, 1.1.1.1"}.get
        self.assertEqual(client_ip(h, "10.0.0.1"), "10.0.0.1")                 # XFF ignored unless proxy trusted
        with env(NOTIV_TRUST_PROXY="1"): self.assertEqual(client_ip(h, "10.0.0.1"), "6.6.6.6")


class TestMediaSecurity(Base):
    def test_hls_disguised_as_mp4_rejected_and_not_fetched(self):
        from core.lecture import validation
        p = os.path.join(self.tmp, "evil.mp4")
        open(p, "w").write("#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:5,\nhttp://127.0.0.1:9/x.ts\n#EXT-X-ENDLIST\n")
        with self.assertRaises(ValidationError): validation.validate_file(p, "evil.mp4", "video/mp4")
        q = os.path.join(self.tmp, "evil2.mp4"); open(q, "w").write("ffconcat version 1.0\nfile '/etc/passwd'\n")
        with self.assertRaises(ValidationError): validation.validate_file(q, "evil2.mp4")


class TestMigrationFileSet(unittest.TestCase):
    def test_postgres_baseline_covers_every_sqlite_table(self):
        import re
        from core.lecture import migrations as M
        tabs = lambda d: {t for m in M.load(d) for t in re.findall(r"CREATE (?:VIRTUAL )?TABLE IF NOT EXISTS (\w+)", m.sql)}
        self.assertEqual(tabs("sqlite") - tabs("postgres"), set())


if __name__ == "__main__":
    unittest.main()
