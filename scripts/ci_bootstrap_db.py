"""Bootstrap a fresh PostgreSQL for the CI integration job (T1.16).

The compose stack applies ``docker/postgres/init`` through the image entrypoint.
A GitHub Actions service container cannot mount that directory, so this script
performs the same steps over asyncpg: create the vector extension, the two
application roles, the ``olist`` schema, the committed platform SQL files and
the grants from ``01-init.sh``. Run it before the Olist loader, because the
loader creates the ``olist`` tables that the default privileges must cover.

    uv run python scripts/ci_bootstrap_db.py

It reads ``SWITCHBOARD_DATABASE_ADMIN_URL``, ``SWITCHBOARD_APP_DB_PASSWORD``
and ``SWITCHBOARD_TOOLS_DB_PASSWORD``, and is safe to run twice.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

import asyncpg

DEFAULT_INIT_DIR = Path("docker/postgres/init")
SQL_FILES = (
    "02-platform.sql",
    "03-documents.sql",
    "04-runs-spans.sql",
    "05-feedback.sql",
    "06-cache.sql",
    "07-sql-tool.sql",
    "08-hitl.sql",
)
ROLES = (
    ("switchboard_app", "SWITCHBOARD_APP_DB_PASSWORD"),
    ("switchboard_tools", "SWITCHBOARD_TOOLS_DB_PASSWORD"),
)

# The non-role statements from 01-init.sh. The default privilege is what lets the
# tool role SELECT the olist tables that the loader creates as the admin user.
GRANTS_SQL = """
GRANT USAGE, CREATE ON SCHEMA public TO switchboard_app;
GRANT USAGE ON SCHEMA public TO switchboard_tools;
GRANT USAGE ON SCHEMA olist TO switchboard_tools;
ALTER DEFAULT PRIVILEGES IN SCHEMA olist GRANT SELECT ON TABLES TO switchboard_tools;
"""


async def create_role(connection: asyncpg.Connection, role: str, password: str) -> None:
    exists = await connection.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role)
    if exists:
        print(f"role {role} already exists")
        return
    literal = await connection.fetchval("SELECT quote_literal($1)", password)
    await connection.execute(f"CREATE ROLE {role} LOGIN PASSWORD {literal}")
    print(f"created role {role}")


async def bootstrap(database_url: str, init_dir: Path) -> None:
    connection = await asyncpg.connect(database_url)
    try:
        await connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
        for role, variable in ROLES:
            password = os.environ.get(variable, "")
            if not password:
                raise SystemExit(f"{variable} is not set")
            await create_role(connection, role, password)
        await connection.execute("CREATE SCHEMA IF NOT EXISTS olist")
        for name in SQL_FILES:
            await connection.execute((init_dir / name).read_text(encoding="utf-8"))
            print(f"applied {name}")
        await connection.execute(GRANTS_SQL)
        print("applied the grants from 01-init.sh")
    finally:
        await connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("SWITCHBOARD_DATABASE_ADMIN_URL", ""),
        help="Admin connection URL; defaults to SWITCHBOARD_DATABASE_ADMIN_URL.",
    )
    parser.add_argument(
        "--init-dir",
        default=str(DEFAULT_INIT_DIR),
        help="Directory holding the committed SQL files.",
    )
    args = parser.parse_args()
    if not args.database_url:
        print("set SWITCHBOARD_DATABASE_ADMIN_URL or pass --database-url")
        return 2
    asyncio.run(bootstrap(args.database_url, Path(args.init_dir)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
