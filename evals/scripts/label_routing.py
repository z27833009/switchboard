"""Label routing questions blind. Standard library only.

Shows one question at a time and records which agents should answer it. It never
reads the generator's labels. Progress is saved after every answer; run it again
to continue where you stopped.

    python evals/scripts/label_routing.py

Do not open routing.generator_labels.jsonl before you have finished.
"""

from __future__ import annotations

import json

from common import DATASET_DIR, read_jsonl

CHOICES = {
    "1": ["direct"],
    "2": ["docs"],
    "3": ["data"],
    "4": ["data", "docs"],
}
HELP = """
  1  direct     no lookup: greetings, thanks, what can you do, small talk
  2  docs       shop policies: returns, shipping, seller onboarding, payments, late delivery compensation
  3  data       order database: counts, late deliveries, one order's details, top products
  4  data+docs  needs both
  q  quit (progress is saved)
"""


def main() -> None:
    questions = read_jsonl(DATASET_DIR / "routing.questions.jsonl")
    out = DATASET_DIR / "routing.author_labels.jsonl"
    done = {row["id"] for row in read_jsonl(out)} if out.exists() else set()
    todo = [q for q in questions if q["id"] not in done]
    print(HELP)
    print(f"{len(done)} labeled, {len(todo)} to go\n")
    with out.open("a", encoding="utf-8", newline="\n") as f:
        for i, q in enumerate(todo, 1):
            print(f"[{len(done) + i}/{len(questions)}] {q['id']}")
            print(f"  {q['query']}")
            while True:
                answer = input("  > ").strip().lower()
                if answer == "q":
                    print("saved, run again to continue")
                    return
                if answer in CHOICES:
                    f.write(json.dumps({"id": q["id"], "agents": CHOICES[answer]}) + "\n")
                    f.flush()
                    print()
                    break
                print(HELP)
    print("all questions labeled; next: python evals/scripts/merge_routing.py")


if __name__ == "__main__":
    main()
