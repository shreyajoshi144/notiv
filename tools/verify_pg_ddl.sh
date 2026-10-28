#!/bin/bash
# Applies the PostgreSQL migrations twice (idempotency) to a throwaway database. Needs psql + a reachable server:
#   PGHOST=localhost PGPORT=55432 PGUSER=postgres tools/verify_pg_ddl.sh      (e.g. docker compose -f docker-compose.sprint5.yml up -d postgres)
set -e; export PGOPTIONS="-c client_min_messages=warning"
DB=notiv_ddl_$$; createdb "$DB"; trap 'dropdb "$DB"' EXIT
for pass in 1 2; do for f in core/lecture/migrations/postgres/*.sql; do psql -v ON_ERROR_STOP=1 -q -d "$DB" -f "$f"; done; done
echo "PASS: $(psql -Atd "$DB" -c "select count(*) from information_schema.tables where table_schema='public'") tables, migrations idempotent"
