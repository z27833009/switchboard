"""Shared definitions for dataset scripts. Standard library only.

These constants mirror design.md sections 6.4 and 7.1. If the tool schemas or the
agent set change there, change them here in the same commit.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

DATASET_DIR = Path(__file__).resolve().parents[1] / "datasets" / "v1"

AGENTS = {"direct", "docs", "data"}
EXCLUSIVE_AGENTS = {"direct"}

# required args are compared exactly by the scorer; optional args only when present
TOOLS: dict[str, dict[str, set[str]]] = {
    "orders_summary@1": {"required": {"date_from", "date_to"}, "optional": {"status"}},
    "orders_late@1": {"required": {"date_from", "date_to"}, "optional": {"min_days_late"}},
    "order_lookup@1": {"required": {"order_id"}, "optional": set()},
    "top_products@1": {"required": {"date_from", "date_to", "metric", "limit"}, "optional": set()},
    "describe_schema@1": {"required": set(), "optional": set()},
}

# Olist order_status values, confirmed against the download on 2026-11-12
STATUSES = {
    "created",
    "approved",
    "invoiced",
    "processing",
    "shipped",
    "delivered",
    "unavailable",
    "canceled",
}
METRICS = {"revenue", "count"}

# Actual range of order_purchase_timestamp in the Olist download (measured 2026-11-12: 2016-09-04 to 2018-10-17).
# Questions are generated inside 2017-01 to 2018-08, the months with more than a few hundred orders.
DATE_MIN = date(2016, 9, 4)
DATE_MAX = date(2018, 10, 17)

DEV_SHARE_MODULUS = 3  # one in three cases is dev


def split_for(case_id: str) -> str:
    """Deterministic dev/test assignment from the case id."""
    digest = hashlib.sha256(case_id.encode("utf-8")).digest()
    return "dev" if digest[0] % DEV_SHARE_MODULUS == 0 else "test"


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise SystemExit(f"{path.name}:{n}: invalid JSON: {e}") from e
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def plan_label(agents: list[str]) -> str:
    return "+".join(sorted(agents))
