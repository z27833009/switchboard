# Olist data

The demo database is loaded from the public Brazilian E-Commerce dataset by
Olist. It is licensed CC BY-NC-SA 4.0, so the full download is not committed:

> Brazilian E-Commerce Public Dataset by Olist,
> https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce,
> licensed CC BY-NC-SA 4.0,
> https://creativecommons.org/licenses/by-nc-sa/4.0/.

A 1000-order subset lives in [sample/](sample/) under the same license, with
its own attribution file.

## Download

1. Open the Kaggle page above and sign in (a free account is enough).
2. Download `archive.zip`.
3. Unzip it so that the nine CSV files sit directly in `data/olist/raw/`.
   `data/olist/raw/` is git-ignored.

Expected files:

| File | Table |
| --- | --- |
| `olist_customers_dataset.csv` | `olist.customers` |
| `olist_geolocation_dataset.csv` | `olist.geolocation` |
| `olist_orders_dataset.csv` | `olist.orders` |
| `olist_order_items_dataset.csv` | `olist.order_items` |
| `olist_order_payments_dataset.csv` | `olist.order_payments` |
| `olist_order_reviews_dataset.csv` | `olist.order_reviews` |
| `olist_products_dataset.csv` | `olist.products` |
| `olist_sellers_dataset.csv` | `olist.sellers` |
| `product_category_name_translation.csv` | `olist.product_category_name_translation` |

## Load

Start the database first (`docker compose up -d postgres`), then run on the
host:

```bash
uv run --env-file .env switchboard data load-olist data/olist/raw
```

The loader connects as the bootstrap database user (`SWITCHBOARD_DATABASE_ADMIN_URL`),
because the application role has no access to schema `olist`. It truncates and
reloads every table, so it is safe to run again. The schema is
[schema.sql](schema.sql).

After loading, the loader compares the row counts with
[expected_counts.json](expected_counts.json). A mismatch names the table and
exits non-zero. Change the baseline only when the source data changes, in the
same commit that records the new numbers here.

## Row counts

Measured by the loader on 2026-11-12 from the full download. The load took 10.2
seconds on the development machine.

| Table | Rows |
| --- | ---: |
| `customers` | 99441 |
| `geolocation` | 1000163 |
| `orders` | 99441 |
| `order_items` | 112650 |
| `order_payments` | 103886 |
| `order_reviews` | 99224 |
| `products` | 32951 |
| `sellers` | 3095 |
| `product_category_name_translation` | 71 |

## Test sample

```bash
uv run python data/olist/extract_sample.py
```

The script picks 1000 orders spread across the sorted order ids and keeps the
related customers, items, payments, reviews, products and sellers. It writes
the subset, `sample/expected_counts.json` and `sample/README.md`, and its output
is deterministic. Because the loader truncates, load the sample into a separate
test database, or reload the full data afterwards:

```bash
uv run --env-file .env switchboard data load-olist data/olist/sample \
  --expected data/olist/sample/expected_counts.json
```
