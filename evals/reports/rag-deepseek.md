# Rag eval: deepseek

## Run

- Dataset: `v3` at `evals/datasets/v3/rag.jsonl`
- Split: `test`, scored 40 of 40 cases in the split (40 in the dataset)
- Orchestrator: `langgraph`; provider: `deepseek`; model: `deepseek-flash`
- Generated: 2026-11-13T12:53:02.881904+00:00
- Wall time: 55.108 s

## Metrics

| Metric | Value |
| --- | --- |
| recall_at_5_query_as_written | 1.0000 |
| mrr_query_as_written | 0.8250 |
| recall_at_5_always_rewrite | 0.9250 |
| mrr_always_rewrite | 0.8708 |
| recall_at_5_conditional_rewrite | 1.0000 |
| mrr_conditional_rewrite | 0.8708 |
| recall_at_5_conditional_hybrid | 1.0000 |
| mrr_conditional_hybrid | 0.8708 |

## Strategies

| Strategy | Recall@5 | MRR | Cases | Rewrite rounds | Retrieval rounds | Cases with rewrite | p50 ms | p95 ms | Errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| query_as_written | 1.0000 | 0.8250 | 40 | 0 | 40 | 0 | 91.0 | 107.1 | 0 |
| always_rewrite | 0.9250 | 0.8708 | 40 | 40 | 40 | 40 | 897.5 | 1228.25 | 0 |
| conditional_rewrite | 1.0000 | 0.8708 | 40 | 4 | 40 | 4 | 89.5 | 882.9 | 0 |
| conditional_hybrid | 1.0000 | 0.8708 | 40 | 4 | 40 | 4 | 110.0 | 913.55 | 0 |

Cases tagged `needs_rewrite`:

| Strategy | Cases | Recall@5 | MRR |
| --- | --- | --- | --- |
| query_as_written | 9 | 1.0000 | 0.6667 |
| always_rewrite | 9 | 1.0000 | 0.9444 |
| conditional_rewrite | 9 | 1.0000 | 0.8704 |
| conditional_hybrid | 9 | 1.0000 | 0.8704 |

All four strategies run on the same cases, in the same order. Recall@5 counts the share of the labeled chunk keys in the first five results; MRR is the mean of 1/rank of the first relevant result. A chunk's score is always its cosine similarity, so the rewrite threshold and the fused order stay separate. The conditional strategies rewrite only when history is present or the best cosine is below the tenant's `retrieval.min_score`, and never make more than two retrieval rounds. The latency columns are wall time per case for that strategy, including the embedding call and any rewrite call.

Cross-encoder re-ranking is not enabled: it is optional in design.md section 9.2, would add sentence-transformers and a torch stack on an 8 GB GPU shared with qwen3-8b and bge-m3, and this run has no measured evidence that it would pay for that cost.

## Latency

| Window | p50 ms | p95 ms | min ms | max ms | mean ms |
| --- | --- | --- | --- | --- | --- |
| Retrieval | 1191.0 | 2708.65 | 901.0 | 3145.0 | 1371.6 |
| Runner wall | 1193.0 | 2709.7 | 903.0 | 3147.0 | 1375.725 |

## Usage

| Input tokens | Cached input tokens | Output tokens | Total tokens |
| --- | --- | --- | --- |
| 6667 | 0 | 316 | 6983 |

Cost: USD 0.002379 for this run, from `config/prices.yaml` (verified on 2026-11-12, https://api-docs.deepseek.com/quick_start/pricing). Cached input tokens are billed at the cached rate.

## Cases (40)

| Case | Query | Needs rewrite | Relevant | query_as_written R@5/MRR | always_rewrite R@5/MRR | conditional_rewrite R@5/MRR | conditional_hybrid R@5/MRR | Rewrites | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| rag001 | I changed my mind, how long do I have to send it back? | yes | returns-policy.md#1 | 1.000/0.500 | 1.000/0.500 | 1.000/0.500 | 1.000/0.500 | 0/1/0/0 |  |
| rag002 | 电子产品可以退货吗？ | yes | returns-policy.md#2 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag003 | Can I still return it? | yes | returns-policy.md#2 | 1.000/0.500 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/1/1 |  |
| rag004 | Can I get my cash back after returning something? | yes | returns-policy.md#3, payments.md#4 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag005 | Can I return a personalized gift? | no | returns-policy.md#4 | 1.000/1.000 | 1.000/0.500 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag006 | How quickly must I report an item that arrived damaged? | no | returns-policy.md#4 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag007 | Who do I contact about a specific order? | no | returns-policy.md#5 | 1.000/0.500 | 1.000/1.000 | 1.000/0.500 | 1.000/0.500 | 0/1/0/0 |  |
| rag008 | Do I need the original packaging to return something? | no | returns-policy.md#0 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag009 | Are return shipping fees refunded? | no | returns-policy.md#3 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag010 | What happens if I return an item without opening a request? | no | returns-policy.md#4 | 1.000/0.500 | 0.000/0.000 | 1.000/0.500 | 1.000/0.500 | 0/1/0/0 |  |
| rag011 | How long does standard delivery take in the southeast? | no | shipping-policy.md#2 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag012 | How fast is express delivery? | no | shipping-policy.md#2 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag013 | When must a seller hand the parcel to the carrier? | no | shipping-policy.md#1 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag014 | It has not moved for two days, what now? | yes | shipping-policy.md#3 | 1.000/0.333 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/1/1 |  |
| rag015 | The parcel says delivered but I cannot find it, what should I do? | no | shipping-policy.md#3 | 1.000/1.000 | 0.000/0.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag016 | Can I change the delivery address after the seller shipped it? | no | shipping-policy.md#5 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag017 | Can I cancel an order that never arrived after 21 days? | no | shipping-policy.md#4 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag018 | How is the estimated delivery date calculated? | no | shipping-policy.md#0 | 1.000/0.333 | 1.000/0.333 | 1.000/0.333 | 1.000/0.333 | 0/1/0/0 |  |
| rag019 | Does a boleto payment delay handling? | no | shipping-policy.md#1, payments.md#3 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag020 | When is a delivery considered late? | no | late-delivery-compensation.md#0 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag021 | 延迟送达有补偿吗？ | yes | late-delivery-compensation.md#1 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag022 | How much is the late delivery voucher worth? | no | late-delivery-compensation.md#2 | 1.000/0.500 | 0.000/0.000 | 1.000/0.500 | 1.000/0.500 | 0/1/0/0 |  |
| rag023 | How do I request late delivery compensation? | no | late-delivery-compensation.md#3 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag024 | How many vouchers do I get if an order has several items? | no | late-delivery-compensation.md#3 | 1.000/0.500 | 1.000/1.000 | 1.000/0.500 | 1.000/0.500 | 0/1/0/0 |  |
| rag025 | Is a refused parcel eligible for late compensation? | no | late-delivery-compensation.md#4 | 1.000/0.333 | 1.000/0.500 | 1.000/0.333 | 1.000/0.333 | 0/1/0/0 |  |
| rag026 | What happens if my address was wrong and the parcel was late? | no | late-delivery-compensation.md#4, late-delivery-compensation.md#1 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag027 | Which payment methods can I use? | no | payments.md#0 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag028 | Can I split the payment? | yes | payments.md#2 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/1/1 |  |
| rag029 | How long does a boleto take to confirm? | no | payments.md#3 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag030 | Where does my refund go if I paid with Pix? | no | payments.md#4 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag031 | What happens in a chargeback dispute? | no | payments.md#4 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag032 | Will support ever ask for my card password? | no | payments.md#5 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag033 | How long does a credit card verification take? | no | payments.md#1 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag034 | Which companies can apply to sell on the marketplace? | no | seller-onboarding.md#0 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag035 | What documents do I need to become a seller? | no | seller-onboarding.md#2 | 1.000/0.333 | 1.000/1.000 | 1.000/0.333 | 1.000/0.333 | 0/1/0/0 |  |
| rag036 | What does the platform keep from each sale? | yes | seller-onboarding.md#4 | 1.000/0.333 | 1.000/1.000 | 1.000/0.333 | 1.000/0.333 | 0/1/0/0 |  |
| rag037 | How often are seller payouts made? | no | seller-onboarding.md#4 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag038 | Can I have more than one seller account? | no | seller-onboarding.md#1 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag039 | What must a listing include before it is published? | no | seller-onboarding.md#3 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/0/0 |  |
| rag040 | Who can I ask about it? | yes | seller-onboarding.md#5 | 1.000/0.333 | 1.000/1.000 | 1.000/1.000 | 1.000/1.000 | 0/1/1/1 |  |

Errors: 0
