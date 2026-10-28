"""Worker lifecycle with the REAL worker loop, real SQLite store and real job service (stages are tiny test stages; the
heavy media pipeline is covered elsewhere): start -> claim -> complete -> heartbeat -> stale-job recovery -> clean stop,
and a mis-configured production worker refuses to start."""
import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

from core.lecture import config, jobs, pipeline, settings, worker
from core.lecture.errors import ValidationError
from core.lecture.testkit import Env


def wait_for(pred, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


class TestWorker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = Env(self.tmp); self.repo = self.env.repo
        self._old = (config._repo, config._storage)
        config.configure(self.repo, self.env.storage)
        self.ran = []
        stage = jobs.Stage("S", lambda ctx: self.ran.append(ctx.job["id"]) or {}, 1)
        self.js = lambda *a, **k: jobs.JobService(self.repo, [stage], lambda: None, backoff_base=0)
        self.p = mock.patch.object(pipeline, "make_job_service", self.js); self.p.start()
        worker._stop.clear()

    def tearDown(self):
        worker.stop()
        t = worker._thread
        if t is not None:
            t.join(5)
        self.p.stop()
        config._repo, config._storage = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_processes_a_queued_job_beats_and_stops_cleanly(self):
        lid = self.env.lectures.create(1, "L")
        jid = self.js().enqueue(lid)
        worker.start(poll=0.02)
        self.assertTrue(wait_for(lambda: self.repo.get_job(jid)["status"] == "completed"))
        self.assertEqual(self.ran, [jid])
        self.assertEqual(self.repo.get_lecture(lid)["status"], "done")
        from core.lecture import store
        self.assertGreaterEqual(store.conn().execute("SELECT COUNT(*) FROM worker_heartbeats").fetchone()[0], 1)   # heartbeat row written
        worker.stop()
        self.assertTrue(wait_for(lambda: not worker._thread.is_alive(), 5), "worker thread did not stop")

    def test_wake_picks_up_a_job_enqueued_while_idle(self):
        worker.start(poll=30)                                           # long poll: only wake() can make this fast
        time.sleep(0.2)
        lid = self.env.lectures.create(1, "L2"); jid = self.js().enqueue(lid)
        worker.wake()
        self.assertTrue(wait_for(lambda: self.repo.get_job(jid)["status"] == "completed", 5))

    def test_stale_running_job_is_recovered_and_completed(self):
        lid = self.env.lectures.create(1, "L3"); jid = self.js().enqueue(lid)
        self.repo.claim_next_job(time.time(), worker_id="dead-worker")           # a worker that then died
        self.repo.update_job(jid, heartbeat_at=1)                                   # long-stale heartbeat
        with mock.patch.object(worker, "HEARTBEAT_TIMEOUT", 60), mock.patch.object(worker, "RECOVER_EVERY", 0.0):
            worker.start(poll=0.02)
            self.assertTrue(wait_for(lambda: self.repo.get_job(jid)["status"] == "completed", 10))
        self.assertEqual(self.repo.get_job(jid)["worker_id"] != "dead-worker", True)

    def test_loop_survives_a_database_error(self):
        calls = {"n": 0}
        real = self.repo.claim_next_job

        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("db blip")
            return real(*a, **k)
        lid = self.env.lectures.create(1, "L4"); jid = self.js().enqueue(lid)
        with mock.patch.object(self.repo, "claim_next_job", flaky):
            worker.start(poll=0.02)
            self.assertTrue(wait_for(lambda: self.repo.get_job(jid)["status"] == "completed", 10))
        self.assertGreaterEqual(calls["n"], 2)

    def test_start_is_idempotent_and_restarts_after_stop(self):
        worker.start(poll=0.02); t1 = worker._thread
        worker.start(poll=0.02); self.assertIs(worker._thread, t1)
        worker.stop(); t1.join(5); self.assertFalse(t1.is_alive())
        worker.start(poll=0.02); self.assertTrue(worker._thread.is_alive() and worker._thread is not t1)


class TestWorkerStartupConfig(unittest.TestCase):
    def test_misconfigured_production_worker_refuses_to_start(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("NOTIV_")}
        env["NOTIV_ENV"] = "production"
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(worker, "_loop") as loop:
            with self.assertRaises(ValidationError):
                worker.main()
            loop.assert_not_called()                                    # never polls with a bad config

    def test_worker_role_does_not_require_api_secrets(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("NOTIV_")}
        env.update(NOTIV_ENV="production", NOTIV_DATABASE_URL="postgresql://u:p@h/d", NOTIV_STORAGE="s3", NOTIV_S3_BUCKET="b")
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual([p for p in settings.problems("worker") if "AUTH" in p or "CORS" in p or "METRICS" in p], [])


if __name__ == "__main__":
    unittest.main()
