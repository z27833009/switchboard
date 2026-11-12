"""Extract a small, deterministic Olist sample for tests and CI.

The full download stays out of the repository (see README.md). This script
picks 1000 orders spread evenly through the sorted order ids and keeps the
customer, item, payment, review, product and seller rows those orders need.
Geolocation is not needed by the five tools, so the sample carries its header
only; that keeps the sample small and the loader's file list complete.

    uv run python data/olist/extract_sample.py

The output is byte-identical on every run. ``sample/expected_counts.json`` is
the row count baseline for the sample and ``sample/README.md`` carries the
attribution required by CC BY-NC-SA 4.0.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

RAW = Path(__file__).resolve().parent / "raw"
OUT = Path(__file__).resolve().parent / "sample"
ORDER_COUNT = 1000
ATTRIBUTION = (
    "Source: Brazilian E-Commerce Public Dataset by Olist, "
    "https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce, "
    "licensed CC BY-NC-SA 4.0, "
    "https://creativecommons.org/licenses/by-nc-sa/4.0/. "
    "This is a subset of the original data, redistributed unchanged under the "
    "same license for noncommercial tests and CI."
)


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = [
            {
                name: (value.replace("\r\n", "\n").replace("\r", "\n") if value else value)
                for name, value in row.items()
            }
            for row in reader
        ]
        return fields, rows


def write_rows(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> int:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fieldnames, lineterminator="\n", quoting=csv.QUOTE_MINIMAL
        )
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def pick_order_ids(order_ids: list[str], count: int) -> set[str]:
    ordered = sorted(order_ids)
    if len(ordered) <= count:
        return set(ordered)
    stride = max(1, len(ordered) // count)
    return set(ordered[::stride][:count])


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    order_fields, orders = read_rows(RAW / "olist_orders_dataset.csv")
    chosen = pick_order_ids([row["order_id"] for row in orders], ORDER_COUNT)
    orders = [row for row in orders if row["order_id"] in chosen]
    customer_ids = {row["customer_id"] for row in orders}

    item_fields, items = read_rows(RAW / "olist_order_items_dataset.csv")
    items = [row for row in items if row["order_id"] in chosen]
    product_ids = {row["product_id"] for row in items}
    seller_ids = {row["seller_id"] for row in items}

    payment_fields, payments = read_rows(RAW / "olist_order_payments_dataset.csv")
    payments = [row for row in payments if row["order_id"] in chosen]

    review_fields, reviews = read_rows(RAW / "olist_order_reviews_dataset.csv")
    reviews = [row for row in reviews if row["order_id"] in chosen]

    customer_fields, customers = read_rows(RAW / "olist_customers_dataset.csv")
    customers = [row for row in customers if row["customer_id"] in customer_ids]

    product_fields, products = read_rows(RAW / "olist_products_dataset.csv")
    products = [row for row in products if row["product_id"] in product_ids]

    seller_fields, sellers = read_rows(RAW / "olist_sellers_dataset.csv")
    sellers = [row for row in sellers if row["seller_id"] in seller_ids]

    translation_fields, translations = read_rows(RAW / "product_category_name_translation.csv")
    geo_fields, _ = read_rows(RAW / "olist_geolocation_dataset.csv")

    counts = {
        "customers": write_rows(OUT / "olist_customers_dataset.csv", customer_fields, customers),
        "geolocation": write_rows(OUT / "olist_geolocation_dataset.csv", geo_fields, []),
        "orders": write_rows(OUT / "olist_orders_dataset.csv", order_fields, orders),
        "order_items": write_rows(OUT / "olist_order_items_dataset.csv", item_fields, items),
        "order_payments": write_rows(
            OUT / "olist_order_payments_dataset.csv", payment_fields, payments
        ),
        "order_reviews": write_rows(
            OUT / "olist_order_reviews_dataset.csv", review_fields, reviews
        ),
        "products": write_rows(OUT / "olist_products_dataset.csv", product_fields, products),
        "sellers": write_rows(OUT / "olist_sellers_dataset.csv", seller_fields, sellers),
        "product_category_name_translation": write_rows(
            OUT / "product_category_name_translation.csv",
            translation_fields,
            translations,
        ),
    }
    (OUT / "expected_counts.json").write_text(
        json.dumps(counts, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    (OUT / "README.md").write_text(
        "# Olist test sample\n\n"
        f"{ATTRIBUTION}\n\n"
        f"Generated by `uv run python data/olist/extract_sample.py` from the full\n"
        f"download. It contains {counts['orders']} orders, chosen by taking every\n"
        f"`len(order_ids) // {ORDER_COUNT}`th id from the sorted order ids.\n\n"
        "| Table | Rows |\n| --- | ---: |\n"
        + "".join(f"| `{name}` | {count} |\n" for name, count in counts.items())
        + "\nThe geolocation file has its header only: the five MCP tools do not use\n"
        "it. `expected_counts.json` is the baseline for this sample. Point the\n"
        "loader at it with `--expected data/olist/sample/expected_counts.json`.\n",
        encoding="utf-8",
        newline="\n",
    )
    for name, count in counts.items():
        print(f"{name:<38}{count:>10}")


if __name__ == "__main__":
    main()
