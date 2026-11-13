"""Load the Olist CSV files into the ``olist`` schema.

The nine source files are public data, not part of the package. The command
``switchboard data load-olist <dir>`` is a development tool that connects with
the bootstrap database user because the application role has no access to the
``olist`` schema (design.md section 14). After a successful load it re-applies
``docker/postgres/init/07-sql-tool.sql`` so the read-only fact view for
``query_readonly_sql@1`` exists on a fresh volume, where the entrypoint ran that
file before the tables did.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

import asyncpg

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_SQL = REPO_ROOT / "data" / "olist" / "schema.sql"
SQL_TOOL_SQL = REPO_ROOT / "docker" / "postgres" / "init" / "07-sql-tool.sql"
DEFAULT_EXPECTED_COUNTS = REPO_ROOT / "data" / "olist" / "expected_counts.json"
COPY_BATCH_SIZE = 50_000

Kind = Literal["text", "int", "numeric", "float", "timestamp"]


@dataclass(frozen=True)
class Column:
    name: str
    kind: Kind


@dataclass(frozen=True)
class Table:
    name: str
    filename: str
    columns: tuple[Column, ...]


def _text(name: str) -> Column:
    return Column(name, "text")


def _int(name: str) -> Column:
    return Column(name, "int")


TABLES: tuple[Table, ...] = (
    Table(
        "customers",
        "olist_customers_dataset.csv",
        (
            _text("customer_id"),
            _text("customer_unique_id"),
            _int("customer_zip_code_prefix"),
            _text("customer_city"),
            _text("customer_state"),
        ),
    ),
    Table(
        "geolocation",
        "olist_geolocation_dataset.csv",
        (
            _int("geolocation_zip_code_prefix"),
            Column("geolocation_lat", "float"),
            Column("geolocation_lng", "float"),
            _text("geolocation_city"),
            _text("geolocation_state"),
        ),
    ),
    Table(
        "orders",
        "olist_orders_dataset.csv",
        (
            _text("order_id"),
            _text("customer_id"),
            _text("order_status"),
            Column("order_purchase_timestamp", "timestamp"),
            Column("order_approved_at", "timestamp"),
            Column("order_delivered_carrier_date", "timestamp"),
            Column("order_delivered_customer_date", "timestamp"),
            Column("order_estimated_delivery_date", "timestamp"),
        ),
    ),
    Table(
        "order_items",
        "olist_order_items_dataset.csv",
        (
            _text("order_id"),
            _int("order_item_id"),
            _text("product_id"),
            _text("seller_id"),
            Column("shipping_limit_date", "timestamp"),
            Column("price", "numeric"),
            Column("freight_value", "numeric"),
        ),
    ),
    Table(
        "order_payments",
        "olist_order_payments_dataset.csv",
        (
            _text("order_id"),
            _int("payment_sequential"),
            _text("payment_type"),
            _int("payment_installments"),
            Column("payment_value", "numeric"),
        ),
    ),
    Table(
        "order_reviews",
        "olist_order_reviews_dataset.csv",
        (
            _text("review_id"),
            _text("order_id"),
            _int("review_score"),
            _text("review_comment_title"),
            _text("review_comment_message"),
            Column("review_creation_date", "timestamp"),
            Column("review_answer_timestamp", "timestamp"),
        ),
    ),
    Table(
        "products",
        "olist_products_dataset.csv",
        (
            _text("product_id"),
            _text("product_category_name"),
            _int("product_name_lenght"),
            _int("product_description_lenght"),
            _int("product_photos_qty"),
            _int("product_weight_g"),
            _int("product_length_cm"),
            _int("product_height_cm"),
            _int("product_width_cm"),
        ),
    ),
    Table(
        "sellers",
        "olist_sellers_dataset.csv",
        (
            _text("seller_id"),
            _int("seller_zip_code_prefix"),
            _text("seller_city"),
            _text("seller_state"),
        ),
    ),
    Table(
        "product_category_name_translation",
        "product_category_name_translation.csv",
        (
            _text("product_category_name"),
            _text("product_category_name_english"),
        ),
    ),
)


def convert(value: str, kind: Kind) -> object:
    if value == "":
        return None
    if kind == "text":
        return value
    if kind == "int":
        return int(value)
    if kind == "numeric":
        return Decimal(value)
    if kind == "float":
        return float(value)
    if kind == "timestamp":
        return datetime.fromisoformat(value)
    raise ValueError(f"unknown column kind {kind!r}")


def records(path: Path, table: Table) -> Iterator[tuple[object, ...]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        expected = [column.name for column in table.columns]
        if reader.fieldnames != expected:
            raise ValueError(f"{path.name}: columns {reader.fieldnames}, expected {expected}")
        for line, row in enumerate(reader, start=2):
            try:
                yield tuple(
                    convert(row.get(column.name) or "", column.kind) for column in table.columns
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path.name}:{line}: {exc}") from exc


def batched(
    rows: Iterable[tuple[object, ...]], size: int = COPY_BATCH_SIZE
) -> Iterator[list[tuple[object, ...]]]:
    batch: list[tuple[object, ...]] = []
    for row in rows:
        batch.append(row)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


async def load_directory(directory: Path, database_url: str) -> dict[str, int]:
    """Truncate and load every table, returning the resulting row counts."""
    missing = [table.filename for table in TABLES if not (directory / table.filename).is_file()]
    if missing:
        raise FileNotFoundError(f"{directory} is missing: {', '.join(missing)}")

    connection = await asyncpg.connect(database_url)
    try:
        await connection.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        names = ", ".join(f"olist.{table.name}" for table in TABLES)
        await connection.execute(f"TRUNCATE {names} CASCADE")
        for table in TABLES:
            columns = [column.name for column in table.columns]
            for batch in batched(records(directory / table.filename, table)):
                await connection.copy_records_to_table(
                    table.name,
                    schema_name="olist",
                    columns=columns,
                    records=batch,
                )
        counts: dict[str, int] = {}
        for table in TABLES:
            value = await connection.fetchval(f"SELECT count(*) FROM olist.{table.name}")
            counts[table.name] = int(value or 0)
        # The fact view for query_readonly_sql@1 reads these tables. A fresh
        # volume runs 07-sql-tool.sql before they exist (it skips itself), so
        # the load applies the idempotent migration again once the data is in.
        await connection.execute(SQL_TOOL_SQL.read_text(encoding="utf-8"))
        return counts
    finally:
        await connection.close()


def read_expected(path: Path) -> dict[str, int] | None:
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {str(name): int(value) for name, value in payload.items()}


def compare_counts(actual: dict[str, int], expected: dict[str, int]) -> dict[str, tuple[int, int]]:
    mismatches: dict[str, tuple[int, int]] = {}
    for name, wanted in expected.items():
        got = actual.get(name)
        if got != wanted:
            mismatches[name] = (wanted, -1 if got is None else got)
    for name in sorted(actual.keys() - expected.keys()):
        mismatches[name] = (0, actual[name])
    return mismatches
