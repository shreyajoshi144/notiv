"""Pure SQL-rewriting tests for the PostgreSQL adapter (no server needed). These verify the TRANSLATION only."""
import unittest
from core.lecture.pg import Row, translate


class TestTranslate(unittest.TestCase):
    def test_placeholders_and_percent(self):
        self.assertEqual(translate("SELECT * FROM t WHERE a=? AND b LIKE '100%?' AND c=?"), "SELECT * FROM t WHERE a=%s AND b LIKE '100%%?' AND c=%s")

    def test_insert_or_ignore(self):
        self.assertEqual(translate("INSERT OR IGNORE INTO chat_conversations(lecture_id,user_id,created_at,updated_at) VALUES(?,?,?,?)"),
                         "INSERT INTO chat_conversations(lecture_id,user_id,created_at,updated_at) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING")

    def test_insert_or_replace(self):
        t = translate("INSERT OR REPLACE INTO study_meta(lecture_id,fingerprint,generated_at) VALUES(?,?,?)")
        self.assertIn("ON CONFLICT (lecture_id) DO UPDATE SET fingerprint=EXCLUDED.fingerprint, generated_at=EXCLUDED.generated_at", t)
        t2 = translate("INSERT OR REPLACE INTO workspace_members(workspace_id,user_id,role) VALUES(?,?,?)")
        self.assertIn("ON CONFLICT (workspace_id, user_id) DO UPDATE SET role=EXCLUDED.role", t2)

    def test_returning_id_for_serial_tables_only(self):
        self.assertTrue(translate("INSERT INTO lectures(owner_id) VALUES(?)").endswith("RETURNING id"))
        self.assertFalse(translate("INSERT INTO lecture_timeline(lecture_id) VALUES(?)").endswith("RETURNING id"))
        self.assertNotIn("RETURNING", translate("INSERT OR IGNORE INTO chat_conversations(lecture_id) VALUES(?)"))

    def test_upsert_and_quoted_end_untouched(self):
        s = 'INSERT INTO study_progress(user_id) VALUES(?) ON CONFLICT(user_id,item_id) DO UPDATE SET attempts=study_progress.attempts+1'
        self.assertEqual(translate(s), s.replace("?", "%s"))
        self.assertIn('"end"', translate('SELECT "end" FROM lecture_segments WHERE id=?'))

    def test_row_interface(self):
        r = Row(["id", "name"], (1, "x"))
        self.assertEqual((r[0], r["name"], dict(r), list(r), len(r)), (1, "x", {"id": 1, "name": "x"}, [1, "x"], 2))


if __name__ == "__main__":
    unittest.main()
