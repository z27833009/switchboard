"""The read-only SQL validator for ``query_readonly_sql@1`` (T3.05).

The validator is the boundary that keeps a model-written string a single read of
``olist.sql_order_facts``. These cases are the accepted shapes and, more
importantly, the refused ones; the integration module proves the same refusals
against the running server and PostgreSQL.
"""

from __future__ import annotations

import pytest
from mcp_servers.olist.sql_guard import (
    MAX_SQL_LENGTH,
    SqlValidationError,
    validate_readonly_sql,
)

GOOD = {
    "select": "SELECT order_id, price FROM olist.sql_order_facts LIMIT 5",
    "aggregate": (
        "SELECT order_status, count(*) AS items, sum(price) AS revenue "
        "FROM olist.sql_order_facts GROUP BY order_status ORDER BY items DESC"
    ),
    "cte": (
        "WITH march AS (SELECT * FROM olist.sql_order_facts "
        "WHERE purchase_date >= '2018-03-01' AND purchase_date < '2018-04-01') "
        "SELECT seller_id, count(*) FROM march GROUP BY seller_id"
    ),
    "union": (
        "SELECT order_id FROM olist.sql_order_facts WHERE order_status = 'canceled' "
        "UNION SELECT order_id FROM olist.sql_order_facts WHERE days_late > 10"
    ),
    "subquery_and_join": (
        "SELECT f.order_id, f.price FROM olist.sql_order_facts AS f "
        "JOIN (SELECT order_id FROM olist.sql_order_facts WHERE days_late > 5) AS late "
        "ON late.order_id = f.order_id"
    ),
    "alias_star": "SELECT f.* FROM olist.sql_order_facts AS f",
    "quoted_relation": 'SELECT * FROM "olist"."sql_order_facts" LIMIT 1',
    "string_with_semicolon": "SELECT 'a;b' AS label FROM olist.sql_order_facts LIMIT 1",
    "trailing_semicolon": "SELECT order_id FROM olist.sql_order_facts;",
    "comment": (
        "SELECT order_id FROM olist.sql_order_facts -- newest first\nORDER BY purchase_date DESC"
    ),
    "date_filter": (
        "SELECT count(*) FROM olist.sql_order_facts "
        "WHERE purchase_date BETWEEN '2018-01-01' AND '2018-03-31' AND delivered_date IS NOT NULL"
    ),
}


@pytest.mark.parametrize("sql", GOOD.values(), ids=list(GOOD))
def test_accepts_reads_of_the_fact_view(sql: str) -> None:
    statement = validate_readonly_sql(sql)
    assert statement
    assert "sql_order_facts" in statement.lower()
    assert not statement.rstrip().endswith(";")


def test_canonical_output_drops_comments_and_trailing_semicolons() -> None:
    assert (
        validate_readonly_sql("SELECT order_id FROM olist.sql_order_facts; -- note")
        == "SELECT order_id FROM olist.sql_order_facts"
    )


BAD = {
    "insert": "INSERT INTO olist.orders (order_id) VALUES ('x')",
    "update": "UPDATE olist.orders SET order_status = 'canceled'",
    "delete": "DELETE FROM olist.orders",
    "create": "CREATE TABLE olist.scratch (id int)",
    "drop": "DROP TABLE olist.orders",
    "alter": "ALTER TABLE olist.orders ADD COLUMN scratch int",
    "truncate": "TRUNCATE olist.orders",
    "grant": "GRANT SELECT ON olist.orders TO switchboard_tools",
    "copy": "COPY (SELECT * FROM olist.orders) TO '/tmp/orders.csv'",
    "call": "CALL olist.some_procedure()",
    "do": "DO $$ BEGIN DELETE FROM olist.orders; END $$",
    "show": "SHOW search_path",
    "explain": "EXPLAIN SELECT * FROM olist.sql_order_facts",
    "select_into": "SELECT * INTO olist.scratch FROM olist.sql_order_facts",
    "for_update": "SELECT * FROM olist.sql_order_facts FOR UPDATE",
    "for_share": "SELECT * FROM olist.sql_order_facts FOR SHARE",
    "multiple": "SELECT 1; SELECT 2",
    "other_table": "SELECT * FROM olist.orders",
    "catalog": "SELECT relname FROM pg_catalog.pg_class",
    "unqualified_view": "SELECT * FROM sql_order_facts",
    "three_part_name": "SELECT * FROM olist.sql_order_facts.order_id",
    "cte_with_delete": (
        "WITH removed AS (DELETE FROM olist.orders RETURNING order_id) SELECT * FROM removed"
    ),
    "set_config": "SELECT set_config('switchboard.seller_ids', '', true)",
    "quoted_set_config": "SELECT \"set_config\"('switchboard.seller_ids', '', true)",
    "qualified_set_config": "SELECT pg_catalog.set_config('switchboard.seller_ids', '', true)",
    "pg_sleep": "SELECT pg_sleep(10)",
    "nextval": "SELECT nextval('some_sequence')",
    "query_to_xml": "SELECT query_to_xml('SELECT * FROM olist.orders', true, false, '')",
    "table_to_xml": "SELECT table_to_xml('olist.orders', true, false, '')",
    "table_function": "SELECT * FROM olist.sql_order_facts, generate_series(1, 10)",
    "values": "VALUES (1)",
    "empty": "   ",
    "comment_only": "-- nothing to run",
}


@pytest.mark.parametrize("sql", BAD.values(), ids=list(BAD))
def test_rejects_everything_that_is_not_a_single_read(sql: str) -> None:
    with pytest.raises(SqlValidationError):
        validate_readonly_sql(sql)


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("DELETE FROM olist.orders", "only SELECT statements are allowed"),
        ("TRUNCATE olist.orders", "only SELECT statements are allowed; got TRUNCATE"),
        ("SELECT 1; SELECT 2", "exactly one SQL statement is allowed"),
        ("SELECT * FROM olist.orders", "only olist.sql_order_facts can be queried"),
        ("SELECT * INTO t FROM olist.sql_order_facts", "SELECT INTO is not allowed"),
        ("SELECT * FROM olist.sql_order_facts FOR UPDATE", "locking clauses"),
        ("SELECT set_config('x', 'y', true)", "function set_config is not allowed"),
        ("SELECT * FROM olist.sql_order_facts", ""),
    ],
)
def test_messages_name_the_reason(sql: str, message: str) -> None:
    if not message:
        assert validate_readonly_sql(sql)
        return
    with pytest.raises(SqlValidationError, match=message):
        validate_readonly_sql(sql)


def test_rejects_unparseable_sql_with_a_concrete_message() -> None:
    with pytest.raises(SqlValidationError, match="not valid PostgreSQL"):
        validate_readonly_sql("SELEC * FROM olist.sql_order_facts")
    with pytest.raises(SqlValidationError, match="not valid PostgreSQL"):
        validate_readonly_sql("SELECT 'unclosed FROM olist.sql_order_facts")


def test_rejects_sql_over_the_length_limit() -> None:
    too_long = (
        "SELECT order_id FROM olist.sql_order_facts WHERE order_id <> '"
        + ("x" * MAX_SQL_LENGTH)
        + "'"
    )
    with pytest.raises(SqlValidationError, match=f"at most {MAX_SQL_LENGTH} characters"):
        validate_readonly_sql(too_long)
