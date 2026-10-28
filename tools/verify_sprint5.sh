#!/bin/bash
# One-command verification. Prints PASS / FAIL / SKIPPED per step; a skipped step means that path is UNVERIFIED.
# Optional env: NOTIV_TEST_DATABASE_URL (PostgreSQL), NOTIV_TEST_S3_ENDPOINT + NOTIV_TEST_S3_BUCKET (S3), PGHOST/PGPORT/PGUSER (psql checks)
cd "$(dirname "$0")/.."
export PYTHONPATH=.:core:backend
if python3 -c "import pytest" 2>/dev/null; then RUN="python3 -m pytest -q"; else RUN="python3 _build_tools/run_ut.py ."; echo "note: pytest not installed -> unittest stand-in runner"; fi
fail=0
step() { name=$1; shift; out=$("$@" 2>&1); rc=$?; if [ $rc -ne 0 ]; then echo "FAIL    $name"; echo "$out" | tail -15; fail=1; elif echo "$out" | grep -qiE "skipped=[1-9]|[1-9][0-9]* skipped"; then echo "PASS+SKIPS $name  ($(echo "$out" | grep -oiE 'skipped=[0-9]+|[0-9]+ skipped' | head -1))"; else echo "PASS    $name"; fi; }
if [ "$RUN" = "python3 -m pytest -q" ]; then
  step "all suites" python3 -m pytest -q core
else
  for m in test_foundation test_api_boundary test_workspace_api test_ask_api test_study_api test_ask test_study test_workspace test_lecture_pipeline \
           test_sprint5_core test_sprint5_ops test_sprint5_http test_notes test_retrieval_modes test_pg_adapter_logic test_worker_lifecycle test_pg_translate test_pg_contract test_s3_integration test_sprint5_fastapi; do step "$m" $RUN $m; done
fi
if command -v psql >/dev/null && [ -n "$PGHOST$PGPORT" ]; then step "postgres DDL (real server, psql)" tools/verify_pg_ddl.sh; step "postgres SQL checks (real server, psql)" tools/verify_pg_sql.sh; else echo "SKIPPED postgres DDL/SQL checks (set PGHOST/PGPORT/PGUSER) -> UNVERIFIED"; fi
if python3 -c "import playwright" 2>/dev/null; then out=$(python3 tools/verify_notes_session_ui.py 2>&1); echo "$out" | grep -q "^ALL PASSED" && echo "PASS    browser UI: notes + session (headless Chromium)" || { echo "$out" | grep -q "^SKIPPED" && echo "SKIPPED browser UI (chromium unavailable) -> UNVERIFIED" || { echo "FAIL    browser UI: notes + session"; echo "$out" | grep "^FAIL" | head; fail=1; }; }; else echo "SKIPPED browser UI tests (playwright not installed) -> UNVERIFIED"; fi
[ -z "$NOTIV_TEST_DATABASE_URL" ] && echo "SKIPPED postgres application contract (NOTIV_TEST_DATABASE_URL unset) -> PostgreSQL app path UNVERIFIED"
[ -z "$NOTIV_TEST_S3_ENDPOINT" ] && echo "SKIPPED real S3 integration (NOTIV_TEST_S3_ENDPOINT unset) -> S3 UNVERIFIED"
python3 -c "import fastapi, httpx" 2>/dev/null || echo "SKIPPED FastAPI wiring tests (fastapi/httpx not installed) -> FastAPI app UNVERIFIED"
exit $fail
