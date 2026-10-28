"""Logic of our PgConnection wrapper and store.conn() reconnect, using a FAKE psycopg-like raw connection.
This verifies OUR code's behaviour on failure (rollback after an error, dropping a dead connection). It does NOT verify
psycopg or a PostgreSQL server (UNVERIFIED here; see core/test_pg_contract.py, which SKIPS without them)."""
import unittest

from core.lecture import pg, store


class IntegrityErr(Exception):
    pass


class FakeCursor:
    description = None
    rowcount = 0

    def execute(self, sql, params=()):
        if "BOOM" in sql:
            raise IntegrityErr("duplicate key")

    def executemany(self, sql, seq):
        raise IntegrityErr("duplicate key")

    def fetchone(self):
        return None


class FakeRaw:
    """Emulates PostgreSQL's 'aborted transaction' rule: after an error every statement fails until rollback()."""
    def __init__(self):
        self.aborted, self.closed, self.broken, self.rollbacks, self.commits = False, False, False, 0, 0

    def cursor(self):
        raw, cur = self, FakeCursor()
        orig = cur.execute

        def guarded(sql, params=()):
            if raw.aborted:
                raise RuntimeError("current transaction is aborted, commands ignored until end of transaction block")
            try:
                orig(sql, params)
            except IntegrityErr:
                raw.aborted = True
                raise
        cur.execute = guarded
        return cur

    def rollback(self):
        self.rollbacks += 1; self.aborted = False

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


class TestPgConnection(unittest.TestCase):
    def test_failed_statement_does_not_poison_the_connection(self):
        raw = FakeRaw(); c = pg.PgConnection(raw)
        with self.assertRaises(IntegrityErr): c.execute("UPDATE jobs SET status=? WHERE id=? BOOM", ("x", 1))
        self.assertGreaterEqual(raw.rollbacks, 1)
        c.execute("SELECT 1")                                   # would raise 'transaction is aborted' without the rollback

    def test_executemany_failure_also_rolls_back(self):
        raw = FakeRaw(); c = pg.PgConnection(raw)
        with self.assertRaises(IntegrityErr): c.executemany("INSERT INTO t(a) VALUES(?)", [(1,)])
        self.assertFalse(raw.aborted or raw.rollbacks == 0)

    def test_is_broken_reflects_closed_or_broken_connection(self):
        raw = FakeRaw(); c = pg.PgConnection(raw)
        self.assertFalse(c.is_broken())
        raw.broken = True; self.assertTrue(c.is_broken())
        raw.broken, raw.closed = False, True; self.assertTrue(c.is_broken())


class TestStoreReconnects(unittest.TestCase):
    def test_conn_drops_a_dead_cached_connection_and_reconnects(self):
        class Dead:
            closed_called = False
            def is_broken(self): return True
            def close(self): Dead.closed_called = True
        old_local = getattr(store._local, "c", None), getattr(store._local, "path", None)
        try:
            store._local.c, store._local.path = Dead(), store.DB_PATH
            c = store.conn()                                    # sqlite dev DB: must build a NEW connection
            self.assertTrue(Dead.closed_called)
            self.assertNotIsInstance(c, Dead)
        finally:
            store._local.c, store._local.path = old_local


if __name__ == "__main__":
    unittest.main()
