#!/bin/bash
# Real-PostgreSQL SQL-level checks (needs psql + server; PGHOST/PGPORT/PGUSER/PGDATABASE as usual). No Python driver needed.
set -e; cd "$(dirname "$0")/.."
python3 tools/pg_sql_check.py > /tmp/notiv_chk.sql
psql -q -At -v ON_ERROR_STOP=1 -f /tmp/notiv_chk.sql 2>&1 | grep -E "CHECK|ERROR" | tee /tmp/notiv_chk.out
grep -q "FAIL\|ERROR" /tmp/notiv_chk.out && { echo "RESULT: FAIL"; exit 1; }; echo "RESULT: PASS (SQL-level only; PgConnection/psycopg path still needs test_pg_contract.py)"
