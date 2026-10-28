"""Emits SQL that applies the PostgreSQL migrations then runs adapter-translated statements (upserts with qualified
columns, RETURNING, tsvector search, partial unique index) so they can be executed by `psql` against a REAL server.
Verifies SQL validity/semantics on PostgreSQL WITHOUT a Python driver. It does NOT verify PgConnection/psycopg code.
usage: python tools/pg_sql_check.py > /tmp/chk.sql ; psql -v ON_ERROR_STOP=0 -f /tmp/chk.sql   (see tools/verify_pg_sql.sh)"""
import os, sys
sys.path[:0] = [os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]
from core.lecture import migrations as M, pg


def lit(sql, *vals):
    t = pg.translate(sql).replace("%%", "%")
    for v in vals:
        t = t.replace("%s", "NULL" if v is None else (repr(v) if isinstance(v, str) else str(v)), 1)
    return t + ";"


print("DROP SCHEMA IF EXISTS notiv_chk CASCADE; CREATE SCHEMA notiv_chk; SET search_path=notiv_chk;")
for m in M.load("postgres"):
    print(m.sql)
print(lit("INSERT INTO lectures(owner_id,title,language,course_id,workspace_id,created_at) VALUES(?,?,?,?,?,?)", 1, "t", "en", None, 1, 1.0))
print(lit('INSERT INTO lecture_evidence(lecture_id,source_type,source_id,start,"end",slide_index,slide_id,source_asset_id,text,confidence,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
          1, "transcript", 1, 1.0, 4.0, None, None, None, "Gradient descent uses the gradient", None, 1.0))
print("INSERT INTO evidence_fts(text,evidence_id,lecture_id) SELECT text,id,lecture_id FROM lecture_evidence WHERE lecture_id=1;")
print(lit("INSERT OR REPLACE INTO study_meta(lecture_id,fingerprint,generated_at) VALUES(?,?,?)", 1, "a", 1.0))
print(lit("INSERT OR REPLACE INTO study_meta(lecture_id,fingerprint,generated_at) VALUES(?,?,?)", 1, "b", 2.0))
for _ in range(2):
    print("INSERT INTO knowledge_reviews(user_id,knowledge_id,count,reviewed_at) VALUES(1,1,1,1) ON CONFLICT(user_id,knowledge_id) DO UPDATE SET count=knowledge_reviews.count+1, reviewed_at=excluded.reviewed_at;")
    print("INSERT INTO study_progress(user_id,item_id,attempts,correct_count,streak,last_correct,last_attempt_at) VALUES(1,1,1,1,1,1,1) ON CONFLICT(user_id,item_id) DO UPDATE SET attempts=study_progress.attempts+1, correct_count=study_progress.correct_count+excluded.correct_count, streak=CASE WHEN excluded.last_correct=1 THEN study_progress.streak+1 ELSE 0 END, last_correct=excluded.last_correct, last_attempt_at=excluded.last_attempt_at;")
print("SELECT CASE WHEN (SELECT count FROM knowledge_reviews)=2 AND (SELECT attempts FROM study_progress)=2 AND (SELECT streak FROM study_progress)=2 THEN 'CHECK upserts: PASS' ELSE 'CHECK upserts: FAIL' END;")
print("SELECT CASE WHEN COUNT(*)=1 AND MAX(snippet) LIKE '[Gradient]%' THEN 'CHECK tsvector search: PASS' ELSE 'CHECK tsvector search: FAIL' END FROM (SELECT ts_headline('english', e.text, to_tsquery('english','gradient'), 'StartSel=[,StopSel=],MaxWords=12,MinWords=3') AS snippet FROM evidence_fts f JOIN lecture_evidence e ON e.id=f.evidence_id WHERE f.tsv @@ to_tsquery('english','gradient') AND e.lecture_id IN (1)) s;")
print("SELECT CASE WHEN COUNT(*)=0 THEN 'CHECK lecture scoping: PASS' ELSE 'CHECK lecture scoping: FAIL' END FROM evidence_fts f WHERE f.tsv @@ to_tsquery('english','gradient') AND f.lecture_id IN (2);")
print("SELECT COUNT(*) n, SUM(CASE WHEN finished_at IS NOT NULL THEN 1 ELSE 0 END) f FROM study_sessions WHERE user_id=1;")
print('SELECT id,start,"end",text,manual_slide FROM lecture_segments WHERE lecture_id=1 ORDER BY start;')
print("INSERT INTO jobs(lecture_id,workspace_id,type,max_attempts,created_at) VALUES(1,1,'p',3,1) RETURNING id;")
print("DO $$ BEGIN INSERT INTO jobs(lecture_id,workspace_id,type,max_attempts,created_at) VALUES(1,1,'p',3,1); RAISE EXCEPTION 'CHECK single active job: FAIL'; EXCEPTION WHEN unique_violation THEN RAISE NOTICE 'CHECK single active job: PASS'; END $$;")
# ── fenced finalization (store.finalize_owned) on real PG: lock the job row, verify lease, write stage+job+lecture atomically
print("UPDATE jobs SET status='running', worker_id='A' WHERE type='p';")
print("BEGIN;")
print("SELECT status, worker_id, lecture_id FROM jobs WHERE type='p' FOR UPDATE;")
print(lit("INSERT INTO job_stages(job_id,stage,status,attempt,started_at) VALUES((SELECT id FROM jobs WHERE type='p'),?,?,?,?)", "S", "completed", 0, 1.0))
print("UPDATE jobs SET status='completed', progress=100 WHERE type='p' AND status='running' AND worker_id='A';")
print("UPDATE lectures SET status='done' WHERE id=1;")
print("COMMIT;")
print("SELECT CASE WHEN (SELECT status FROM jobs WHERE type='p')='completed' AND (SELECT status FROM lectures WHERE id=1)='done' AND (SELECT count(*) FROM job_stages)=1 THEN 'CHECK fenced finalize txn: PASS' ELSE 'CHECK fenced finalize txn: FAIL' END;")
print("DO $$ DECLARE n int; BEGIN UPDATE jobs SET status='failed' WHERE type='p' AND status='running' AND worker_id='B'; GET DIAGNOSTICS n = ROW_COUNT; IF n=0 THEN RAISE NOTICE 'CHECK fenced write rejects non-owner: PASS'; ELSE RAISE EXCEPTION 'CHECK fenced write rejects non-owner: FAIL'; END IF; END $$;")
# ── notes (migration 0003): CRUD, link snapshot survives evidence replacement (no FK), cascade-by-purge, per-user scoping
print("INSERT INTO user_notes(user_id,lecture_id,workspace_id,body,slide_index,t,created_at,updated_at) VALUES(1,1,1,'n1',2,3,1,1) RETURNING id;")
print("INSERT INTO user_notes(user_id,lecture_id,workspace_id,body,created_at,updated_at) VALUES(2,1,1,'other',1,1);")
print("INSERT INTO user_note_links(note_id,evidence_id,quote,slide_index,start) VALUES(1,1,'Gradient descent',NULL,1.0);")
print("DELETE FROM lecture_evidence WHERE id=1;")
print("SELECT CASE WHEN (SELECT count(*) FROM user_note_links WHERE note_id=1)=1 AND (SELECT count(*) FROM user_notes WHERE user_id=1)=1 THEN 'CHECK notes link survives evidence replacement: PASS' ELSE 'CHECK notes link survives evidence replacement: FAIL' END;")
print("DO $$ BEGIN INSERT INTO user_note_links(note_id,evidence_id,quote) VALUES(1,1,'dup'); RAISE EXCEPTION 'CHECK notes duplicate link: FAIL'; EXCEPTION WHEN unique_violation THEN RAISE NOTICE 'CHECK notes duplicate link rejected: PASS'; END $$;")
print("DELETE FROM user_note_links WHERE note_id IN (SELECT id FROM user_notes WHERE lecture_id=1); DELETE FROM user_notes WHERE lecture_id=1;")
print("SELECT CASE WHEN (SELECT count(*) FROM user_notes)=0 AND (SELECT count(*) FROM user_note_links)=0 THEN 'CHECK notes purge: PASS' ELSE 'CHECK notes purge: FAIL' END;")
print("DROP SCHEMA notiv_chk CASCADE;")
