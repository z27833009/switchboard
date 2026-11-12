# Gate eval: mock

## Run

- Dataset: `v2` at `evals/datasets/v2/gate.jsonl`
- Split: `test`, scored 30 of 30 cases in the split (30 in the dataset)
- Orchestrator: `langgraph`; provider: `mock`; model: `deterministic`
- Generated: 2026-11-12T21:05:05.969875+00:00
- Wall time: 0.019 s
- Deterministic suite: no model call is made; the provider and model fields are recorded from the runner but unused.

## Metrics

| Metric | Value |
| --- | --- |
| numbers_grounded_precision | 0.9000 |
| numbers_grounded_recall | 1.0000 |
| numbers_grounded_f1 | 0.9474 |
| citations_valid_precision | 1.0000 |
| citations_valid_recall | 1.0000 |
| citations_valid_f1 | 1.0000 |
| evidence_present_precision | 1.0000 |
| evidence_present_recall | 1.0000 |
| evidence_present_f1 | 1.0000 |
| merge_complete_precision | 1.0000 |
| merge_complete_recall | 1.0000 |
| merge_complete_f1 | 1.0000 |
| case_accuracy | 0.9667 |
| gate_precision | 0.9412 |
| gate_recall | 1.0000 |
| good_case_specificity | 0.9286 |

## Checks

| Check | TP | FP | FN | TN | Precision | Recall |
| --- | --- | --- | --- | --- | --- | --- |
| numbers_grounded | 9 | 1 | 0 | 20 | 0.9000 | 1.0000 |
| citations_valid | 3 | 0 | 0 | 27 | 1.0000 | 1.0000 |
| evidence_present | 4 | 0 | 0 | 26 | 1.0000 | 1.0000 |
| merge_complete | 2 | 0 | 0 | 28 | 1.0000 | 1.0000 |

Labels: the cases and their expected failed checks were constructed and read through by the implementation session, not by an independent human annotator. Precision and recall measure the deterministic checks against those labels; they are not a claim about real-world answer quality. The derived-percentage case is labeled acceptable on purpose, so its false rejection is visible instead of hidden (ADR 0013).

Predicted weak cases: 17; labeled weak cases: 16; false positive ids: g013; false negative ids: none.

## Latency

| Window | p50 ms | p95 ms | min ms | max ms | mean ms |
| --- | --- | --- | --- | --- | --- |
| Model | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| Runner wall | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |

## Usage

| Input tokens | Cached input tokens | Output tokens | Total tokens |
| --- | --- | --- | --- |
| 0 | 0 | 0 | 0 |

Cost is not reported as USD: no price table exists until T2.06, so the JSON value is null. A local model such as LM Studio has no API cost.

## Cases (30)

| Case | Query | Expected checks | Actual checks | Action | Weak | Correct | Note |
| --- | --- | --- | --- | --- | --- | --- | --- |
| g001 | What is the electronics return window? | none | none | pass | no | yes | Clear good docs answer: one grounded number, one valid citation, evidence present. |
| g002 | How quickly must a seller ship an order? | none | none | pass | no | yes | Good docs answer with a number word, not a digit, and a valid citation. |
| g003 | What is the electronics return window? | numbers_grounded | numbers_grounded | retry | yes | yes | The 30 is absent from the evidence; the 15 in the answer would be grounded but the 30 is not. |
| g004 | What is the electronics return window? | citations_valid | citations_valid | retry | yes | yes | The number is grounded and evidence exists, but the docs answer has no marker. |
| g005 | What is the electronics return window? | citations_valid | citations_valid | retry | yes | yes | The marker points to d9, which the agent was never given. |
| g006 | How many orders were delivered late in March 2018? | none | none | pass | no | yes | Good data answer: the thousands separator in the answer does not break grounding. |
| g007 | How many orders were delivered late in March 2018? | numbers_grounded | numbers_grounded | retry | yes | yes | 1,500 is absent from the result, which reports 1234. |
| g008 | What share of orders was delivered late? | none | none | pass | no | yes | Good data answer: percent spacing and the percent unit normalize to the same token. |
| g009 | What share of orders was delivered late? | numbers_grounded | numbers_grounded | retry | yes | yes | The answer states 3.4% where the result says 3.5%. |
| g010 | When was order 044bcd01474ba715e7cb11d5a70ccf0e delivered? | none | none | pass | no | yes | Good data answer: the date parts normalize and the hex order id is not a number. |
| g011 | What was the item price for order 044bcd01474ba715e7cb11d5a70ccf0e? | none | none | pass | no | yes | Good data answer: 74.90 and 74.9 normalize to the same decimal. |
| g012 | What was the item price for order 044bcd01474ba715e7cb11d5a70ccf0e? | numbers_grounded | numbers_grounded | retry | yes | yes | The answer states 79.90 where the result says 74.9. |
| g013 | How many of the 100 orders were delivered late? | none | numbers_grounded | retry | no | no | Derived-number false-positive case, labeled acceptable on purpose: 50% is the correct share of 50 out of 100 but is not written in the evidence. ADR 0013 documents this limitation. |
| g014 | Does the 30 day return window apply to electronics? | none | none | pass | no | yes | Good docs answer: the 30 comes from the query and is excluded from grounding; the 15 is in the evidence. |
| g015 | What is the electronics return window? | numbers_grounded | numbers_grounded | retry | yes | yes | 45 is absent from the document. |
| g016 | How many orders were delivered late in March 2018? | evidence_present, numbers_grounded | evidence_present, numbers_grounded | replan | yes | yes | No evidence at all: the number is unsupported and the agent saw nothing. citations_valid does not double-count because there is no evidence to cite. |
| g017 | What is the compensation for a late delivery? | evidence_present | evidence_present | replan | yes | yes | Isolates evidence_present: the answer has no numbers and no citation requirement when no evidence exists. |
| g018 | Were there any late orders in January 2015? | evidence_present | evidence_present | replan | yes | yes | The tool result was empty, so it is not usable evidence even though a ref exists. |
| g019 | How many orders were delivered in March 2018? | evidence_present | evidence_present | replan | yes | yes | Every tool was denied, so the agent saw no evidence. |
| g020 | Were late deliveries in March 2018 eligible for compensation, and how many were late? | none | none | pass | no | yes | Good merged answer: both sources present, all numbers grounded, both markers valid. |
| g021 | Were late deliveries in March 2018 eligible for compensation, and how many were late? | merge_complete | merge_complete | none | yes | yes | The plan required data and docs, but only docs produced a result and the answer does not name the missing source. |
| g022 | Were late deliveries in March 2018 eligible for compensation, and how many were late? | merge_complete | merge_complete | none | yes | yes | The data source is missing but named. The check still fails; the honest note is the correct output. |
| g023 | What compensation applies to a late delivery? | none | none | pass | no | yes | Good docs answer with a percent. |
| g024 | What compensation applies to a late delivery? | numbers_grounded | numbers_grounded | retry | yes | yes | 10% is absent from the document. |
| g025 | What was the total revenue from sellers in 2017? | none | none | pass | no | yes | Good data answer: the million unit normalizes to 1500000. |
| g026 | What was the total revenue from sellers in 2017? | numbers_grounded | numbers_grounded | retry | yes | yes | 2.5 million is 2500000, which is absent from the result. |
| g027 | How are refunds issued? | none | none | pass | no | yes | Good docs answer with no digits at all. |
| g028 | What can you do? | none | none | pass | no | yes | Good direct answer: the evidence checks do not apply and the plan has one source, so no merge check applies. |
| g029 | What is the electronics return window? | citations_valid, numbers_grounded | citations_valid, numbers_grounded | retry | yes | yes | Two failures at once: 45 is absent and d9 was never given. |
| g030 | What is the maximum number of items in one order? | none | none | pass | no | yes | Good docs answer: the thousands separator in the answer normalizes to the evidence number. |

Errors: 0
