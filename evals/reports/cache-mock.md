# Cache eval: mock

## Run

- Dataset: `v5` at `evals/datasets/v5/cache.jsonl`
- Split: `test`, scored 50 of 50 cases in the split (50 in the dataset)
- Orchestrator: `langgraph`; provider: `mock`; model: `mock (deterministic)`
- Generated: 2026-11-13T01:03:34.896242+00:00
- Wall time: 0.043 s
- Embeddings: mock (deterministic sha256, dimension 1024)
- **Deterministic mock embeddings:** this run checks the suite mechanics only. A hash-based fake has no semantic similarity, so its hit and false-hit rates are not a quality measurement.

## Metrics

| Metric | Value |
| --- | --- |
| pair_count | 50.0000 |
| positive_pairs | 25.0000 |
| negative_pairs | 25.0000 |
| hit_rate_at_0.85 | 0.0000 |
| false_hit_rate_at_0.85 | 0.0000 |
| accuracy_at_0.85 | 0.5000 |
| hit_rate_at_0.88 | 0.0000 |
| false_hit_rate_at_0.88 | 0.0000 |
| accuracy_at_0.88 | 0.5000 |
| hit_rate_at_0.90 | 0.0000 |
| false_hit_rate_at_0.90 | 0.0000 |
| accuracy_at_0.90 | 0.5000 |
| hit_rate_at_0.92 | 0.0000 |
| false_hit_rate_at_0.92 | 0.0000 |
| accuracy_at_0.92 | 0.5000 |
| hit_rate_at_0.94 | 0.0000 |
| false_hit_rate_at_0.94 | 0.0000 |
| accuracy_at_0.94 | 0.5000 |
| hit_rate_at_0.96 | 0.0000 |
| false_hit_rate_at_0.96 | 0.0000 |
| accuracy_at_0.96 | 0.5000 |
| hit_rate_at_0.98 | 0.0000 |
| false_hit_rate_at_0.98 | 0.0000 |
| accuracy_at_0.98 | 0.5000 |
| hit_rate_at_0.99 | 0.0000 |
| false_hit_rate_at_0.99 | 0.0000 |
| accuracy_at_0.99 | 0.5000 |

## Threshold sweep

| Threshold | Hits | Positive pairs | Hit rate | False hits | Negative pairs | False-hit rate | Accuracy |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.85 | 0 | 25 | 0.0000 | 0 | 25 | 0.0000 | 0.5000 |
| 0.88 | 0 | 25 | 0.0000 | 0 | 25 | 0.0000 | 0.5000 |
| 0.90 | 0 | 25 | 0.0000 | 0 | 25 | 0.0000 | 0.5000 |
| 0.92 | 0 | 25 | 0.0000 | 0 | 25 | 0.0000 | 0.5000 |
| 0.94 (chosen) | 0 | 25 | 0.0000 | 0 | 25 | 0.0000 | 0.5000 |
| 0.96 | 0 | 25 | 0.0000 | 0 | 25 | 0.0000 | 0.5000 |
| 0.98 | 0 | 25 | 0.0000 | 0 | 25 | 0.0000 | 0.5000 |
| 0.99 | 0 | 25 | 0.0000 | 0 | 25 | 0.0000 | 0.5000 |

A pair is a hit when its cosine similarity is at or above the threshold. Hit rate is the share of pairs labeled `should_hit` that hit; the false-hit rate is the share of near-miss pairs that hit anyway. The configured threshold is 0.94. The value in `config/tenants.yaml` and the comment in `config/platform.yaml` point at this report. Both questions of every pair are embedded once before the cases run, so the runner wall maximum is that batch and a case's own similarity is immediate.

## Latency

| Window | p50 ms | p95 ms | min ms | max ms | mean ms |
| --- | --- | --- | --- | --- | --- |
| Similarity | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| Runner wall | 0.0 | 0.0 | 0.0 | 6.0 | 0.12 |

## Usage

| Input tokens | Cached input tokens | Output tokens | Total tokens |
| --- | --- | --- | --- |
| 0 | 0 | 0 | 0 |

Cost is not reported: provider `mock` has no entry in `config/prices.yaml`, so the JSON value is null rather than an invented zero. A mock replay makes no billable API call.

## Cases (50)

| Case | Query A | Query B | Should hit | Pair type | Similarity | Predicted hit | Correct | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| c001 | What is the return window for electronics? | How long do I have to return an electronic item? | yes | paraphrase | 0.0141 | no | no | Same 15 day electronics window.; classified wrong at the chosen threshold |
| c002 | How many days do I have to return a normal item? | What is the standard return period after delivery? | yes | paraphrase | 0.0438 | no | no | Same 30 calendar day standard window.; classified wrong at the chosen threshold |
| c003 | When are late deliveries eligible for compensation? | When can I claim compensation for a late delivery? | yes | paraphrase | 0.0000 | no | no | Same three calendar day eligibility rule.; classified wrong at the chosen threshold |
| c004 | How much is the late delivery voucher worth? | What is the value of the late-delivery compensation? | yes | paraphrase | 0.0008 | no | no | Same ten percent voucher rule.; classified wrong at the chosen threshold |
| c005 | How long does a refund take after the item is inspected? | When will I get my money back after returning an item? | yes | paraphrase | 0.0000 | no | no | Same five business day refund after inspection.; classified wrong at the chosen threshold |
| c006 | Can I pay with boleto? | Is a bank slip accepted as payment? | yes | paraphrase | 0.0000 | no | no | Boleto is the bank slip.; classified wrong at the chosen threshold |
| c007 | How many installments can I pay in? | Can I split the order into monthly payments? | yes | paraphrase | 0.0000 | no | no | Same installment policy.; classified wrong at the chosen threshold |
| c008 | How do I start selling on the marketplace? | What is the process to become a seller? | yes | paraphrase | 0.0007 | no | no | Same onboarding path.; classified wrong at the chosen threshold |
| c009 | Which documents does a new seller need to upload? | What paperwork is required for seller onboarding? | yes | paraphrase | 0.0291 | no | no | Same document list.; classified wrong at the chosen threshold |
| c010 | What is the commission rate for sellers? | How much does the marketplace charge per sale? | yes | paraphrase | 0.0472 | no | no | Same twelve percent commission.; classified wrong at the chosen threshold |
| c011 | How long does standard delivery take in the southeast? | What is the usual delivery time in the southeast region? | yes | paraphrase | 0.0053 | no | no | Same three to seven business day range.; classified wrong at the chosen threshold |
| c012 | Can I track my parcel? | Where can I see the tracking information for my order? | yes | paraphrase | 0.0072 | no | no | Same tracking code on the order page.; classified wrong at the chosen threshold |
| c013 | What happens if a boleto is not paid? | Is an unpaid bank slip canceled automatically? | yes | paraphrase | 0.0000 | no | no | Same automatic cancellation after the due date.; classified wrong at the chosen threshold |
| c014 | Are personalized items returnable? | Can customized products be returned? | yes | paraphrase | 0.0558 | no | no | Same exception for personalized items.; classified wrong at the chosen threshold |
| c015 | How soon must a damaged item be reported? | What is the deadline to report a damaged delivery? | yes | paraphrase | 0.0000 | no | no | Same 48 hour rule.; classified wrong at the chosen threshold |
| c016 | How many days after the estimated date is a delivery considered late? | When does a delivery count as late for compensation? | yes | paraphrase | 0.0284 | no | no | Same three calendar day threshold.; classified wrong at the chosen threshold |
| c017 | What is the maximum voucher for late delivery? | What is the compensation cap for a late parcel? | yes | paraphrase | 0.0359 | no | no | Same fifty reais cap.; classified wrong at the chosen threshold |
| c018 | Can a voucher be combined with a promotion? | May I use the late-delivery voucher together with another offer? | yes | paraphrase | 0.0517 | no | no | Same one other promotion rule.; classified wrong at the chosen threshold |
| c019 | How long does a seller have to hand the parcel to the carrier? | What is the seller handling deadline after payment? | yes | paraphrase | 0.0382 | no | no | Same two business day handling rule.; classified wrong at the chosen threshold |
| c020 | Is express delivery available? | Can I choose a faster shipping option? | yes | paraphrase | 0.0353 | no | no | Same express option for selected sellers.; classified wrong at the chosen threshold |
| c021 | How long do seller documents take to be checked? | When are seller registration documents approved? | yes | paraphrase | 0.0000 | no | no | Same three business day check.; classified wrong at the chosen threshold |
| c022 | What payment methods does the marketplace accept? | Which ways can I pay for an order? | yes | paraphrase | 0.0017 | no | no | Same card, boleto and Pix list.; classified wrong at the chosen threshold |
| c023 | When are payouts made to sellers? | How often do sellers receive their money? | yes | paraphrase | 0.0146 | no | no | Same every two weeks payout.; classified wrong at the chosen threshold |
| c024 | Do shipping fees get refunded for a defective item? | Will the delivery cost be returned when the product is faulty? | yes | paraphrase | 0.0095 | no | no | Same refund of shipping for a defect.; classified wrong at the chosen threshold |
| c025 | What is the refund timing for a Pix payment? | How long does a Pix refund take to arrive? | yes | paraphrase | 0.0248 | no | no | Same Pix refund to the paying account.; classified wrong at the chosen threshold |
| c026 | How many orders were delivered in March 2018? | How many orders were delivered in April 2018? | no | near_miss | 0.0969 | no | yes | A different month has a different count. |
| c027 | What is the return window for electronics? | What is the standard return window? | no | near_miss | 0.0000 | no | yes | 15 days versus 30 days. |
| c028 | What is the return window for electronics? | What is the return window for furniture? | no | near_miss | 0.0518 | no | yes | Furniture follows the standard window, not the electronics one. |
| c029 | What is the late delivery compensation policy? | How many late deliveries were there in March 2018? | no | cross_type | 0.0130 | no | yes | Policy text versus a database count. |
| c030 | How many orders were delivered late in March 2018? | How many orders were delivered late in April 2018? | no | near_miss | 0.0207 | no | yes | A different month has a different count. |
| c031 | How many orders were delivered late in March 2018? | How many orders were delivered on time in March 2018? | no | near_miss | 0.0427 | no | yes | Late and on-time counts are different. |
| c032 | How much is the late delivery voucher worth? | How many late deliveries were compensated in March 2018? | no | cross_type | 0.0153 | no | yes | Voucher value versus a compensated count. |
| c033 | How do I start selling on the marketplace? | How many sellers joined the marketplace in 2017? | no | cross_type | 0.0330 | no | yes | Onboarding steps versus a seller count. |
| c034 | How many installments can I pay in? | How many installments are interest free? | no | near_miss | 0.0396 | no | yes | The maximum count and the interest-free count differ. |
| c035 | How long does standard delivery take in the southeast? | How long does express delivery take in the southeast? | no | near_miss | 0.0000 | no | yes | Express is faster than standard. |
| c036 | What is the commission rate for sellers? | Which seller paid the highest commission in 2018? | no | cross_type | 0.0212 | no | yes | A rate versus a seller ranking. |
| c037 | What is the return window for electronics? | How many electronics were returned in 2017? | no | cross_type | 0.0536 | no | yes | A policy window versus a return count. |
| c038 | Can I track my parcel? | How long can tracking take to update? | no | near_miss | 0.0000 | no | yes | Tracking availability versus the 24 hour update lag. |
| c039 | Are personalized items returnable? | Are digital codes returnable? | no | near_miss | 0.0000 | no | yes | Two different exceptions, both normally not returnable. |
| c040 | How soon must a damaged item be reported? | How soon must a late delivery compensation request be opened? | no | near_miss | 0.0131 | no | yes | 48 hours for damage versus 60 days for compensation. |
| c041 | What is the minimum late delivery voucher? | What is the maximum late delivery voucher? | no | near_miss | 0.0000 | no | yes | Five reais minimum versus fifty reais maximum. |
| c042 | Can a voucher be exchanged for cash? | Can a voucher be combined with a promotion? | no | near_miss | 0.0000 | no | yes | Two different voucher rules. |
| c043 | How long does a seller have to hand the parcel to the carrier? | How long does the carrier take to deliver the parcel? | no | near_miss | 0.0119 | no | yes | Seller handling time versus carrier delivery time. |
| c044 | What payment methods does the marketplace accept? | How many orders were paid by boleto in 2018? | no | cross_type | 0.0000 | no | yes | A methods list versus an order count. |
| c045 | Is express delivery available? | Is international delivery available? | no | near_miss | 0.0000 | no | yes | Express is domestic; the corpus does not offer international shipping. |
| c046 | How long do seller documents take to be checked? | How long does a refund take after inspection? | no | near_miss | 0.0255 | no | yes | Three business days for documents versus five for a refund. |
| c047 | What payment methods does the marketplace accept? | Where is a refund sent for a Pix payment? | no | near_miss | 0.0102 | no | yes | Accepted methods versus the refund destination. |
| c048 | When are payouts made to sellers? | When is a late delivery voucher credited to the customer? | no | near_miss | 0.0000 | no | yes | Every two weeks for payouts versus three business days for a voucher. |
| c049 | How many orders were delivered in March 2018? | How many orders were shipped in March 2018? | no | near_miss | 0.0168 | no | yes | Delivered and shipped counts differ in the same month. |
| c050 | How long does delivery take to remote areas? | How long does delivery take to the southeast region? | no | near_miss | 0.0000 | no | yes | Up to twenty business days for remote areas versus three to seven in the southeast. |

Errors: 0
