"""Sprint 5 (partial delivery) tests: migrations, settings, observability. SQLite only."""
import os, sqlite3, tempfile, unittest
from core.lecture import migrations as M, obs, settings, store


class Migrations(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_fresh_db_reaches_head(self):
        store.use_db(self.d + "/a.db")
        st = M.status(store.conn(), "sqlite")
        self.assertTrue(st["ok"]); self.assertEqual(st["current"], M.head_version("sqlite"))

    def test_legacy_sprint4_db_upgrades(self):
        p = self.d + "/legacy.db"
        c = sqlite3.connect(p); c.executescript(store.SCHEMA); c.execute("INSERT INTO lectures(owner_id,title,created_at) VALUES(1,'old',0)"); c.commit(); c.close()
        store.use_db(p)
        self.assertTrue(M.status(store.conn(), "sqlite")["ok"])
        self.assertEqual(store.get_lecture(1)["title"], "old")

    def test_idempotent_and_drift_detected(self):
        store.use_db(self.d + "/b.db"); c = store.conn()
        self.assertEqual(M.upgrade(c, "sqlite"), [])
        c.execute("UPDATE schema_migrations SET checksum='x' WHERE version=1"); c.commit()
        with self.assertRaises(M.MigrationError):
            M.upgrade(c, "sqlite")

    def test_duplicate_active_jobs_collapsed_then_unique(self):
        p = self.d + "/c.db"; c = sqlite3.connect(p); c.row_factory = sqlite3.Row
        M.upgrade(c, "sqlite"); c.execute("DROP INDEX ux_jobs_active"); c.execute("DELETE FROM schema_migrations WHERE version=2")
        for _ in range(2):
            c.execute("INSERT INTO jobs(lecture_id,workspace_id,type,status,created_at) VALUES(1,1,'p','queued',0)")
        c.commit(); M.upgrade(c, "sqlite")
        self.assertEqual(c.execute("SELECT COUNT(*) FROM jobs WHERE status='queued'").fetchone()[0], 1)
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO jobs(lecture_id,workspace_id,type,status,created_at) VALUES(1,1,'p','queued',0)")

    def test_dialects_have_same_versions(self):
        self.assertEqual([m.name for m in M.load("sqlite")], [m.name for m in M.load("postgres")])


class Settings(unittest.TestCase):
    def setUp(self):
        self.old = dict(os.environ)
        for k in [k for k in os.environ if k.startswith("NOTIV_")]: del os.environ[k]
    def tearDown(self):
        os.environ.clear(); os.environ.update(self.old)

    def test_dev_is_permissive_prod_fails_closed(self):
        self.assertEqual(settings.problems(), [])
        os.environ["NOTIV_ENV"] = "production"
        self.assertGreaterEqual(len(settings.problems()), 3)
        with self.assertRaises(Exception): settings.validate_or_raise()

    def test_secret_file(self):
        f = tempfile.NamedTemporaryFile("w", delete=False); f.write("aB3$kQ9!zL7#pR2@vN8^wM5&xT1*yU4%\n"); f.close()
        os.environ["NOTIV_AUTH_SECRET_FILE"] = f.name
        self.assertTrue(settings.env("NOTIV_AUTH_SECRET").startswith("aB3"))

    def test_weak_secret_rejected_in_prod(self):
        os.environ.update(NOTIV_ENV="production", NOTIV_AUTH_SECRET="a" * 40)
        self.assertTrue(any("weak" in p for p in settings.problems()))


    def _prod(self, **kv):
        os.environ.update(NOTIV_ENV="production", NOTIV_AUTH_SECRET="aB3$kQ9!zL7#pR2@vN8^wM5&xT1*yU4%", **kv)
        return [p for p in settings.problems("api") if "RATELIMIT" in p or "SINGLE_INSTANCE" in p]

    def test_prod_rate_limit_must_be_shared_or_explicitly_single_instance(self):
        self.assertTrue(self._prod())                                                    # default memory, undeclared -> refused
        self.assertEqual(self._prod(NOTIV_RATELIMIT_BACKEND="db"), [])                   # shared across replicas
        self.assertEqual(self._prod(NOTIV_RATELIMIT_BACKEND="memory", NOTIV_SINGLE_INSTANCE="1"), [])   # explicit single process

    def test_prod_single_instance_contradicted_by_replicas(self):
        self.assertTrue(self._prod(NOTIV_SINGLE_INSTANCE="1", NOTIV_REPLICAS="3"))       # 3 replicas + per-process counters
        self.assertTrue(self._prod(NOTIV_SINGLE_INSTANCE="1", WEB_CONCURRENCY="4"))      # 4 uvicorn workers = 4 processes
        self.assertEqual(self._prod(NOTIV_RATELIMIT_BACKEND="db", NOTIV_REPLICAS="3", WEB_CONCURRENCY="4"), [])

    def test_prod_cannot_disable_rate_limiting(self):
        self.assertTrue(any("disabled" in p for p in self._prod(NOTIV_RATELIMIT_BACKEND="db", NOTIV_RATELIMIT="off")))

    def test_dev_is_unaffected(self):
        self.assertEqual(settings.problems("api"), [])


class Collection(unittest.TestCase):
    """Regression: importing/collecting the HTTP + study test modules must NOT build the fixture world (ffmpeg encodes)
    or run any subprocess; fixtures are built lazily in setUpClass, with bounded ffmpeg timeouts."""
    def test_collect_does_not_generate_fixtures_or_spawn_processes(self):
        import subprocess, sys, textwrap
        code = textwrap.dedent("""
            import importlib, subprocess, sys
            import core.study_world as sw
            def boom(*a, **k): raise SystemExit("FIXTURE WORLD BUILT AT IMPORT")
            sw.get_world = boom
            def nope(*a, **k): raise SystemExit("SUBPROCESS AT IMPORT")
            subprocess.run = nope; subprocess.Popen = nope
            for m in ("core.test_study", "core.test_study_api", "core.test_sprint5_http", "core.test_ask_api", "core.test_workspace_api"):
                importlib.import_module(m)
            print("collected")
        """)
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([root, os.path.join(root, "core"), os.path.join(root, "backend")]))
        r = subprocess.run([sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("collected", r.stdout)

    def test_fixture_ffmpeg_calls_are_bounded(self):
        import inspect
        from core.lecture import make_test_lecture
        src = inspect.getsource(make_test_lecture)
        self.assertEqual(src.count("subprocess.run("), src.count("timeout=FFMPEG_TIMEOUT"))
        self.assertGreater(make_test_lecture.FFMPEG_TIMEOUT, 0)


class Obs(unittest.TestCase):
    def test_redaction(self):
        r = obs.redact("x /home/u/f.mp4 Bearer abcdefghij?sig=zzz postgresql://u:pw@h/d")
        for leak in ("/home/u", "abcdefghij", "zzz", ":pw@"): self.assertNotIn(leak, r)
    def test_request_id_rejects_injection(self):
        self.assertNotEqual(obs.new_request_id("a\nb"), "a\nb")
        self.assertEqual(obs.new_request_id("req-12345678"), "req-12345678")
    def test_metrics(self):
        obs.reset_metrics(); obs.inc("t_total", route="/x"); self.assertIn('t_total{route="/x"} 1', obs.render_prometheus())

if __name__ == "__main__":
    unittest.main()
