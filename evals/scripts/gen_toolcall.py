"""Generate the toolcall dataset from templates. Standard library only.

Expected arguments are computed by code from the same parameters that fill the
question template, so labels are correct by construction. The author reviews the
wording, not the labels.

    python evals/scripts/gen_toolcall.py --orders-csv data/olist/raw/olist_orders_dataset.csv

Without --orders-csv the order_lookup cases are skipped and a warning is printed.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import random
import sys
from datetime import date, timedelta
from pathlib import Path

from common import DATASET_DIR, write_jsonl

SEED = 20261112
MONTHS_EN = list(calendar.month_name)


class Rotator:
    """Hands out items in shuffled order and reshuffles when all were used, so templates rotate instead of repeating."""

    def __init__(self, rng: random.Random, items: list) -> None:
        self.rng, self.items, self.queue = rng, list(items), []

    def next(self):
        if not self.queue:
            self.queue = self.items[:]
            self.rng.shuffle(self.queue)
        return self.queue.pop()


_rotators: dict[int, Rotator] = {}


def pick(rng: random.Random, items: list):
    key = id(items)
    if key not in _rotators:
        _rotators[key] = Rotator(rng, items)
    return _rotators[key].next()


PERIOD_KINDS = ["month", "month", "quarter", "year", "range", "day", "half"]


def period(rng: random.Random) -> tuple[str, str, date, date]:
    """Return (english phrase, chinese phrase, date_from, date_to) inside 2017-01 to 2018-08."""
    kind = pick(rng, PERIOD_KINDS)
    if kind == "month":
        y = rng.choice([2017, 2018])
        m = rng.randint(1, 12 if y == 2017 else 8)
        last = calendar.monthrange(y, m)[1]
        return f"in {MONTHS_EN[m]} {y}", f"{y}年{m}月", date(y, m, 1), date(y, m, last)
    if kind == "quarter":
        y, q = rng.choice([(2017, 1), (2017, 2), (2017, 3), (2017, 4), (2018, 1), (2018, 2)])
        m0 = 3 * (q - 1) + 1
        end = date(y, m0 + 2, calendar.monthrange(y, m0 + 2)[1])
        zh_q = "一二三四"[q - 1]
        return f"in Q{q} {y}", f"{y}年第{zh_q}季度", date(y, m0, 1), end
    if kind == "year":
        return "in 2017", "2017年全年", date(2017, 1, 1), date(2017, 12, 31)
    if kind == "half":
        return "in the first half of 2018", "2018年上半年", date(2018, 1, 1), date(2018, 6, 30)
    if kind == "range":
        start = date(2017, 1, 1) + timedelta(days=rng.randint(0, 560))
        end = start + timedelta(days=rng.choice([6, 9, 13, 20]))
        return (f"between {start} and {end}", f"{start}到{end}之间", start, end)
    d = date(2017, 1, 1) + timedelta(days=rng.randint(0, 590))
    return f"on {d}", f"{d.year}年{d.month}月{d.day}日", d, d


STATUS_PHRASES = {
    "delivered": ("delivered", "已送达"),
    "shipped": ("shipped", "已发货"),
    "canceled": ("canceled", "已取消"),
    "invoiced": ("invoiced", "已开票"),
}

STATUS_KEYS = list(STATUS_PHRASES)
METRIC_KEYS = ["revenue", "count"]

SUMMARY_EN = [
    "How many orders were placed {p}?",
    "Give me an order summary {p}.",
    "What did order volume look like {p}?",
]
SUMMARY_STATUS_EN = [
    "How many orders were {s} {p}?",
    "Count the {s} orders {p}.",
]
SUMMARY_ZH = ["{p}一共有多少订单？", "统计一下{p}的订单情况"]
SUMMARY_STATUS_ZH = ["{p}有多少{s}的订单？"]

LATE_EN = [
    "How many orders arrived late {p}?",
    "Show me late deliveries {p}.",
    "Which orders were delivered after the estimated date {p}?",
]
LATE_MIN_EN = ["How many orders were at least {n} days late {p}?", "List deliveries that were {n} or more days late {p}."]
LATE_ZH = ["{p}有多少订单延迟送达？", "查一下{p}的延迟订单"]
LATE_MIN_ZH = ["{p}延迟至少{n}天的订单有多少？"]

TOP_EN = {
    "revenue": ["Top {n} products by revenue {p}?", "Which {n} products made the most money {p}?"],
    "count": ["What were the {n} best-selling products by units sold {p}?", "Top {n} products by number of items sold {p}"],
}
TOP_ZH = {
    "revenue": ["{p}销售额最高的前{n}个商品是哪些？"],
    "count": ["{p}销量前{n}的商品有哪些？"],
}

LOOKUP_EN = ["What is the status of order {id}?", "Show me the details of order {id}.", "Where is my order {id}?"]
LOOKUP_ZH = ["帮我查一下订单{id}的情况"]

SCHEMA_CASES = [
    ("What data can you query?", []),
    ("Which tables and fields are available in the order database?", []),
    ("你能查询哪些数据？", ["zh"]),
    ("Describe the database schema you have access to.", []),
]


def build(rng: random.Random, order_ids: list[str]) -> list[dict]:
    cases: list[dict] = []
    seen: set[str] = set()

    def add(query: str, tool: str, args: dict, zh: bool) -> bool:
        if query in seen:
            return False
        seen.add(query)
        cases.append({
            "query": query,
            "expected_tool": tool,
            "expected_args": args,
            "tool_version": "1",
            "tags": ["zh"] if zh else [],
        })
        return True

    def iso(d: date) -> str:
        return d.isoformat()

    counter = {"n": 0}

    def next_zh() -> bool:
        """Every third generated question is Chinese."""
        counter["n"] += 1
        return counter["n"] % 3 == 0

    # orders_summary: 10, four with status, about a third in Chinese
    made = 0
    while made < 10:
        zh = next_zh()
        p_en, p_zh, d0, d1 = period(rng)
        args = {"date_from": iso(d0), "date_to": iso(d1)}
        if made < 4:
            status = pick(rng, STATUS_KEYS)
            s_en, s_zh = STATUS_PHRASES[status]
            args["status"] = status
            q = pick(rng, SUMMARY_STATUS_ZH).format(p=p_zh, s=s_zh) if zh else pick(rng, SUMMARY_STATUS_EN).format(p=p_en, s=s_en)
        else:
            q = pick(rng, SUMMARY_ZH).format(p=p_zh) if zh else pick(rng, SUMMARY_EN).format(p=p_en)
        made += add(q, "orders_summary@1", args, zh)

    # orders_late: 10, four with min_days_late
    made = 0
    while made < 10:
        zh = next_zh()
        p_en, p_zh, d0, d1 = period(rng)
        args = {"date_from": iso(d0), "date_to": iso(d1)}
        if made < 4:
            n = rng.choice([3, 5, 7, 10, 14])
            args["min_days_late"] = n
            q = pick(rng, LATE_MIN_ZH).format(p=p_zh, n=n) if zh else pick(rng, LATE_MIN_EN).format(p=p_en, n=n)
        else:
            q = pick(rng, LATE_ZH).format(p=p_zh) if zh else pick(rng, LATE_EN).format(p=p_en)
        made += add(q, "orders_late@1", args, zh)

    # top_products: 10
    made = 0
    while made < 10:
        zh = next_zh()
        p_en, p_zh, d0, d1 = period(rng)
        metric = pick(rng, METRIC_KEYS)
        n = rng.choice([3, 5, 10, 20])
        args = {"date_from": iso(d0), "date_to": iso(d1), "metric": metric, "limit": n}
        q = pick(rng, TOP_ZH[metric]).format(p=p_zh, n=n) if zh else pick(rng, TOP_EN[metric]).format(p=p_en, n=n)
        made += add(q, "top_products@1", args, zh)

    # order_lookup: 6, real ids only
    for oid in order_ids:
        zh = next_zh()
        q = pick(rng, LOOKUP_ZH if zh else LOOKUP_EN).format(id=oid)
        add(q, "order_lookup@1", {"order_id": oid}, zh)

    # describe_schema: 4
    for q, tags in SCHEMA_CASES:
        add(q, "describe_schema@1", {}, "zh" in tags)

    for i, case in enumerate(cases, 1):
        case["id"] = f"t{i:03d}"
    return [{"id": c["id"], **{k: v for k, v in c.items() if k != "id"}} for c in cases]


def sample_order_ids(path: Path, rng: random.Random, k: int = 6) -> list[str]:
    with path.open(encoding="utf-8", newline="") as f:
        ids = [row["order_id"] for row in csv.DictReader(f)]
    return rng.sample(ids, k)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--orders-csv", type=Path, help="olist_orders_dataset.csv, for order_lookup cases")
    parser.add_argument("--out", type=Path, default=DATASET_DIR / "toolcall.jsonl")
    args = parser.parse_args()

    rng = random.Random(SEED)
    order_ids: list[str] = []
    if args.orders_csv:
        order_ids = sample_order_ids(args.orders_csv, random.Random(SEED + 1))
    else:
        print("warning: no --orders-csv given, order_lookup cases skipped (34 cases instead of 40)", file=sys.stderr)

    cases = build(rng, order_ids)
    write_jsonl(args.out, cases)
    print(f"wrote {len(cases)} cases to {args.out}")


if __name__ == "__main__":
    main()
