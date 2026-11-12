"""Merge blind labels into routing.jsonl. Standard library only.

The author's label is the expected answer. Where the generator's label differs,
the case is tagged `ambiguous` and the generator's label is kept as
`generator_agents`, so the report can show both. Each case gets a deterministic
dev or test split.

    python evals/scripts/merge_routing.py
"""

from __future__ import annotations

from collections import Counter

from common import DATASET_DIR, plan_label, read_jsonl, split_for, write_jsonl


def main() -> None:
    questions = read_jsonl(DATASET_DIR / "routing.questions.jsonl")
    author = {r["id"]: r["agents"] for r in read_jsonl(DATASET_DIR / "routing.author_labels.jsonl")}
    generator = {r["id"]: r["agents"] for r in read_jsonl(DATASET_DIR / "routing.generator_labels.jsonl")}

    adj_path = DATASET_DIR / "routing.adjudication.jsonl"
    adjudicated = {r["id"]: r["agents"] for r in read_jsonl(adj_path)} if adj_path.exists() else {}

    missing = [q["id"] for q in questions if q["id"] not in author]
    if missing:
        raise SystemExit(f"not labeled yet: {missing}; run label_routing.py first")

    rows = []
    disagreements = []
    pairs = Counter()
    for q in questions:
        a = sorted(author[q["id"]])
        g = sorted(generator[q["id"]])
        tags = list(q.get("tags", []))
        final = sorted(adjudicated.get(q["id"], a))
        row = {"id": q["id"], "query": q["query"], "expected_agents": final}
        if a != g:
            # ambiguity is decided by the blind labels, before any adjudication
            tags.append("ambiguous")
            row["generator_agents"] = g
            disagreements.append((q["id"], plan_label(a), plan_label(g), q["query"]))
            pairs[(plan_label(a), plan_label(g))] += 1
        if q["id"] in adjudicated:
            if a == g:
                raise SystemExit(f"{q['id']}: adjudication is only for cases where the blind labels disagree")
            row["author_initial_agents"] = a
        row["tags"] = tags
        row["split"] = split_for(q["id"])
        rows.append(row)

    write_jsonl(DATASET_DIR / "routing.jsonl", rows)
    agree = len(rows) - len(disagreements)
    print(f"wrote {len(rows)} cases; agreement {agree}/{len(rows)} ({agree / len(rows):.0%})")
    if disagreements:
        print("\ndisagreements (author vs generator), tagged ambiguous:")
        for cid, a, g, text in disagreements:
            print(f"  {cid}  {a:10} vs {g:10}  {text}")
        print("\nmost common disagreement pairs:", dict(pairs.most_common(5)))
    if adjudicated:
        print(f"\nadjudicated after review: {sorted(adjudicated)}")
    print("\nnext: python evals/scripts/validate.py routing")


if __name__ == "__main__":
    main()
