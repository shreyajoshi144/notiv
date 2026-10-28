"""PostgreSQL contract test — SKIPPED unless NOTIV_TEST_DATABASE_URL points at a real server AND psycopg is installed.
It was NOT run in the authoring sandbox (no driver). Run: NOTIV_TEST_DATABASE_URL=postgresql://postgres:notiv@localhost:55432/notiv
(docker compose -f docker-compose.sprint5.yml up -d postgres) then `pytest core/test_pg_contract.py`.
The URL is exported as NOTIV_DATABASE_URL in a SUBPROCESS so the store binds to PostgreSQL (a unique schema per run)."""
import os
import subprocess
import sys
import textwrap
import unittest

URL = os.getenv("NOTIV_TEST_DATABASE_URL", "")
try:
    import psycopg  # noqa: F401
    HAVE = True
except ImportError:
    HAVE = False

SCRIPT = textwrap.dedent('''
    import sys, time, threading
    sys.path[:0] = ["."]
    from core.lecture import store, migrations, ops_store, study_store
    c = store.conn(); assert store.DIALECT == "postgres"
    st = migrations.status(c, "postgres"); assert st["ok"], st
    ws = store.ensure_personal_workspace(1); l = store.create_lecture(1, "pg lecture", workspace_id=ws)
    assert store.get_lecture(l)["title"] == "pg lecture"
    # idempotent enqueue + single-claim under concurrency
    a, created = store.create_job_once(l, ws, "process_lecture"); b, c2 = store.create_job_once(l, ws, "process_lecture")
    assert (a == b) and created and not c2
    got = []; ts = [threading.Thread(target=lambda: got.append(store.claim_next_job(time.time(), worker_id="w%d" % i))) for i in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sum(1 for g in got if g) == 1, got
    # evidence + full-text search (tsvector) + "end" quoting
    store.save_results(l, [], [], [])
    ids = store.replace_evidence(l, [dict(source_type="transcript", source_id=1, text="Gradient descent uses the gradient", start=1.0, end=4.0)])
    store.rebuild_index(l)
    hits = store.search_evidence([l], "gradient", 5)
    assert hits and hits[0]["evidence_id"] == ids[0], hits
    assert store.search_evidence([l + 999], "gradient", 5) == []                      # lecture scoping
    # upserts with qualified columns
    k = store.chat_conversation(l, 1, create=True); assert k
    ops_store.rl_hit("b", 1); assert ops_store.rl_hit("b", 1) == 2
    study_store.session_counts(1, "lecture", l)
    store.delete_lecture_data(l); assert store.get_lecture(l) is None and ops_store.outbox_pending()
    print("PG-CONTRACT-OK")
''')


@unittest.skipUnless(URL and HAVE, "NOTIV_TEST_DATABASE_URL and psycopg required (PostgreSQL path UNVERIFIED without them)")
class TestPostgresContract(unittest.TestCase):
    def test_store_contract(self):
        env = {**os.environ, "NOTIV_DATABASE_URL": URL, "NOTIV_PG_SCHEMA": "t_" + str(os.getpid()), "PYTHONPATH": ".:core:backend"}
        r = subprocess.run([sys.executable, "-c", SCRIPT], env=env, capture_output=True, text=True, timeout=120)
        self.assertIn("PG-CONTRACT-OK", r.stdout, r.stdout[-800:] + r.stderr[-1500:])


if __name__ == "__main__":
    unittest.main()
