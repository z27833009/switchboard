# Brief: routing questions for dataset v1

You are writing test questions for the customer assistant of a fictional online marketplace in Brazil. The shop's order data covers 2016 to 2018. Your questions will be used to test whether the assistant sends each question to the right capability. You write the questions and, in a separate file, your own opinion of the right capability. Someone else will label the same questions without seeing your labels.

Read only this file. Do not open any other file in the repository.

## The three capabilities

- **direct**: answers without looking anything up. Greetings, thanks, goodbyes, questions about what the assistant can do, small talk. Never used for facts about the shop's orders or policies.
- **docs**: looks up the shop's written policies. There are five policy documents:
  - returns and refunds (including return windows that differ by product category)
  - shipping (carriers, delivery times, shipping fees)
  - seller onboarding (how to become a seller, requirements, fees)
  - payment methods (cards, boleto, installments, vouchers)
  - late delivery compensation (when a customer is compensated and how)
- **data**: queries the order database. It can answer: order counts and summaries for a date range (optionally by status such as delivered, shipped, canceled), late deliveries for a date range (optionally at least N days late), the details of one order by its order id, and top products by revenue or by units sold for a date range.

Some questions need **data and docs together**, for example a question that needs numbers from the database and a rule from a policy to interpret them.

## What to write

60 questions with ids `r001` to `r060`. Aim for this distribution by your own labels:

| Label | Count |
| --- | --- |
| `["direct"]` | 10 |
| `["docs"]` | 16 |
| `["data"]` | 18 |
| `["data", "docs"]` | 16 |

Within those 60:

- **12 in simplified Chinese**, tag `zh`. The rest in English.
- **At least 10 trap questions**, tag `trap`: the wording points to one capability but the right one is another. For example a question that mentions "orders" but asks about a rule, or a policy word in a question that really asks for a number.
- **About 4 with a small typo or informal spelling**, tag `typo`.
- Vary the style: short and long, casual and formal, direct questions and requests ("show me", "I need"), customer voice and seller voice.

Rules:

- Use absolute dates between January 2017 and August 2018. No "last month", "this year" or "recently".
- A question about one specific order includes an order id written as 32 lowercase hexadecimal characters. Invent them; they do not need to exist.
- Stay within the capabilities above. No questions about product reviews, customer accounts or anything the three capabilities cannot answer.
- Do not use these three questions or close paraphrases, because they are reserved for the demo:
  - "What is the return window for electronics?"
  - "How many orders were delivered late in March 2018?"
  - "Were late deliveries in March eligible for compensation?"
- Mix the labels in the file order. Do not group questions by label, and do not let the order hint at the label.
- Do not explain or hint at the label inside a question.

## Output

Write two files, UTF-8, one JSON object per line:

`evals/datasets/v1/routing.questions.jsonl`

```
{"id": "r001", "query": "...", "tags": ["zh"]}
```

Only the fields `id`, `query` and `tags`. Tags come only from `zh`, `trap`, `typo`; use `[]` for none.

`evals/datasets/v1/routing.generator_labels.jsonl`

```
{"id": "r001", "agents": ["data", "docs"]}
```

`agents` is one of `["direct"]`, `["docs"]`, `["data"]`, `["data", "docs"]`.

Then run `python evals/scripts/validate.py questions` and fix every error. Report the final distribution and nothing else. Do not show the labels in your reply, because the person labeling may read it.
