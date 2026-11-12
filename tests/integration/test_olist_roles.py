"""The tool role reads Olist data and cannot write it (AC-02b).

Skipped unless SWITCHBOARD_TOOLS_DATABASE_URL points at the compose database
with the data loaded. CI runs it once the PostgreSQL service exists (T1.16).
"""

import os

import asyncpg
import pytest

pytestmark = pytest.mark.integration

TOOLS_URL = os.environ.get("SWITCHBOARD_TOOLS_DATABASE_URL", "")


@pytest.mark.skipif(not TOOLS_URL, reason="set SWITCHBOARD_TOOLS_DATABASE_URL")
async def test_tools_role_can_select_but_not_write() -> None:
    connection = await asyncpg.connect(TOOLS_URL)
    try:
        count = await connection.fetchval("SELECT count(*) FROM olist.orders")
        assert count and count > 0

        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.execute(
                "INSERT INTO olist.customers (customer_id, customer_unique_id) "
                "VALUES ('probe', 'probe')"
            )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.execute("CREATE TABLE olist.probe (id integer)")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.execute("DELETE FROM olist.orders")
    finally:
        await connection.close()
