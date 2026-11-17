#!/bin/bash
# One-command verification. Prints PASS / FAIL / SKIPPED per step; a skipped step means that path is UNVERIFIED.
# Needs: pip install -r requirements-test.txt
# Optional env: NOTIV_TEST_DATABASE_URL (PostgreSQL), NOTIV_TEST_S3_ENDPOINT + NOTIV_TEST_S3_BUCKET (S3),
#               PGHOST/PGPORT/PGUSER (psql checks). docker-compose.sprint5.yml starts both locally.
cd "$(dirname "$0")/.."
export PYTHONPATH=.:core:backend
fail=0
step() { name=$1; shift; out=$("$@" 2>&1); rc=$?; if [ $rc -ne 0 ]; then echo "FAIL    $name"; echo "$out" | tail -15; fail=1; elif echo "$out" | grep -qiE "[1-9][0-9]* skipped"; then echo "PASS+SKIPS $name  ($(echo "$out" | grep -oiE '[0-9]+ skipped' | head -1))"; else echo "PASS    $name"; fi; }
python3 -c "import pytest, fastapi, httpx" 2>/dev/null || { echo "FAIL    install requirements-test.txt first"; exit 1; }
step "all suites" python3 -m pytest -q core
if command -v psql >/dev/null && [ -n "$PGHOST$PGPORT" ]; then step "postgres DDL (real server, psql)" tools/verify_pg_ddl.sh; step "postgres SQL checks (real server, psql)" tools/verify_pg_sql.sh; else echo "SKIPPED postgres DDL/SQL checks (set PGHOST/PGPORT/PGUSER) -> UNVERIFIED"; fi
if python3 -c "import playwright" 2>/dev/null; then step "browser end-to-end (tools/e2e_redesign.py)" python3 tools/e2e_redesign.py --out "${TMPDIR:-/tmp}/notiv-e2e"; else echo "SKIPPED browser e2e (pip install playwright && playwright install chromium) -> UNVERIFIED"; fi
[ -z "$NOTIV_TEST_DATABASE_URL" ] && echo "SKIPPED postgres application contract (NOTIV_TEST_DATABASE_URL unset) -> PostgreSQL app path UNVERIFIED"
[ -z "$NOTIV_TEST_S3_ENDPOINT" ] && echo "SKIPPED real S3 integration (NOTIV_TEST_S3_ENDPOINT unset) -> S3 UNVERIFIED"
exit $fail
