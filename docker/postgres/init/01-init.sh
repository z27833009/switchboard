#!/usr/bin/env bash
# Runs once, when the postgres volume is empty. Creates the two application
# roles and the schemas from design.md section 14.
set -euo pipefail

: "${SWITCHBOARD_APP_DB_PASSWORD:?SWITCHBOARD_APP_DB_PASSWORD is missing}"
: "${SWITCHBOARD_TOOLS_DB_PASSWORD:?SWITCHBOARD_TOOLS_DB_PASSWORD is missing}"

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=app_password="$SWITCHBOARD_APP_DB_PASSWORD" \
  --set=tools_password="$SWITCHBOARD_TOOLS_DB_PASSWORD" <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;

-- The API role owns the platform tables in public and has no access to olist.
CREATE ROLE switchboard_app LOGIN PASSWORD :'app_password';
-- The tool server role reads olist and nothing else (INSERT on tool_audit is
-- granted by the migration that creates that table).
CREATE ROLE switchboard_tools LOGIN PASSWORD :'tools_password';

CREATE SCHEMA IF NOT EXISTS olist;

GRANT USAGE, CREATE ON SCHEMA public TO switchboard_app;
GRANT USAGE ON SCHEMA public TO switchboard_tools;
GRANT USAGE ON SCHEMA olist TO switchboard_tools;

-- The loader connects as the bootstrap user, so its tables inherit this grant.
ALTER DEFAULT PRIVILEGES IN SCHEMA olist GRANT SELECT ON TABLES TO switchboard_tools;
SQL
