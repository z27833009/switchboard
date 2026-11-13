# Routing eval: deepseek

## Run

- Dataset: `v1` at `evals/datasets/v1/routing.jsonl`
- Split: `test`, scored 39 of 39 cases in the split (60 in the dataset)
- Orchestrator: `langgraph`; provider: `deepseek`; model: `deepseek-flash`
- Generated: 2026-11-13T12:45:50.522830+00:00
- Wall time: 41.261 s

## Metrics

| Metric | Value |
| --- | --- |
| plan_accuracy | 0.9487 |
| plan_jaccard | 0.9615 |
| case_pass_rate | 0.9487 |
| unambiguous_plan_accuracy | 1.0000 |
| ambiguous_plan_accuracy | 0.6000 |

## Latency

| Window | p50 ms | p95 ms | min ms | max ms | mean ms |
| --- | --- | --- | --- | --- | --- |
| Model | 1054.0 | 1301.5 | 682.0 | 1390.0 | 1055.282 |
| Runner wall | 1054.0 | 1301.5 | 682.0 | 1390.0 | 1055.41 |

## Usage

| Input tokens | Cached input tokens | Output tokens | Total tokens |
| --- | --- | --- | --- |
| 23660 | 14592 | 3003 | 26663 |

Cost: USD 0.006412 for this run, from `config/prices.yaml` (verified on 2026-11-12, https://api-docs.deepseek.com/quick_start/pricing). Cached input tokens are billed at the cached rate.

## Plan confusion matrix

| Expected \ Actual | data | data+docs | direct | docs |
| --- | --- | --- | --- | --- |
| data | 11 | 1 | 0 | 0 |
| data+docs | 0 | 10 | 0 | 0 |
| direct | 0 | 0 | 5 | 0 |
| docs | 0 | 0 | 1 | 11 |

## Cases (39)

| Case | Query | Expected | Actual | Exact | Jaccard | Latency ms | Tokens in/cached/out | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| r003 | Give me an order summary for September 2017 broken down by status. | data | data | yes | 1.000 | 1243 | 603/0/82 | This asks for counts of orders grouped by status for a specific month, which is a record/aggregate query answered by the data agent with no policy component. |
| r008 | my packge d24b9ff725ca614bc99aa08a07f8a06c came super late, do i get anything back for that? | data | data+docs | no | 0.500 | 1061 | 622/384/79 | The question needs the specific order's delivery record (data) plus the late-delivery compensation policy (docs) to determine eligibility. |
| r009 | Order dfe367de1f1445f0ea5f00e2f3e04abb arrived late. Am I eligible for compensation? | data+docs | data+docs | yes | 1.000 | 1141 | 619/384/82 | The question requires looking up the specific order's delivery details (data) and applying the late-delivery compensation policy (docs) to determine eligibility. |
| r010 | Can you look up orders for me, or do you only answer policy questions? | direct | direct | yes | 1.000 | 1360 | 604/384/77 | The user is asking about the assistant's own capabilities, which is a small-talk/capability question answered by the direct agent alone. |
| r012 | 退货后多久能收到退款？ | docs | docs | yes | 1.000 | 975 | 596/384/74 | The question asks about the refund timeline policy after a return, which is answered by the policy documents in the docs agent. |
| r015 | If my order arrives late, what compensation am I entitled to? | docs | docs | yes | 1.000 | 1146 | 601/384/74 | The question asks about the late-delivery compensation policy, which is answered from the document index without needing any order records. |
| r016 | Was order 07460fefc54c9c13abb2bc6ca25e0d17 paid in installments, and if so, what are the installment rules? | data+docs | data+docs | yes | 1.000 | 1207 | 626/384/78 | The question requires looking up the specific order's payment record (data) and the policy text describing installment rules (docs). |
| r017 | As a seller, I'd like to know which products brought in the most revenue in December 2017. | data | data | yes | 1.000 | 1390 | 610/384/80 | The question asks for a revenue ranking of products in a specific month, which requires record and aggregate lookups from the data tools and no policy text. |
| r018 | What kinds of questions can you help me with? | docs | direct | no | 0.000 | 1122 | 598/384/76 | The user is asking about the assistant's own capabilities, which is a small-talk/meta question handled by the direct agent alone. |
| r019 | What fees do sellers pay to join the platform? | docs | docs | yes | 1.000 | 1054 | 598/384/68 | The question asks about seller onboarding fees, which is policy information covered by the document index. |
| r020 | Show me the top 10 products by revenue between June 1 and June 30, 2018. | data | data | yes | 1.000 | 933 | 610/384/80 | This is a ranking/aggregate query over product revenue for a date range, which requires the data agent's read-only tools and no policy text. |
| r023 | Which products sold the most units from October through December 2017? | data | data | yes | 1.000 | 1031 | 602/384/75 | This is a ranking of product sales totals over a date range, which requires the read-only data tools and no policy text. |
| r024 | What is the status of order 66c737db5f67fdd09273a03a6dc911d8? | data | data | yes | 1.000 | 958 | 615/384/75 | The question asks for the status of a specific order, which requires a record lookup from the data agent and no policy text. |
| r025 | 2017年12月延迟交付的订单中，有多少符合补偿条件？ | data+docs | data+docs | yes | 1.000 | 1003 | 605/384/89 | The question requires identifying December 2017 late-delivered orders from the records (data) and applying the late-delivery compensation eligibility policy (docs) to count how many qualify. |
| r026 | Please pull order c975ae74962ebff9c04ea5ab827c90c5 and explain which payment rules apply to the method it used. | data+docs | data+docs | yes | 1.000 | 840 | 621/384/83 | The question requires looking up a specific order's payment method (data) and then explaining the payment policy rules that apply to that method (docs). |
| r027 | Please explain how refunds work when an item arrives damaged. | docs | docs | yes | 1.000 | 899 | 600/384/78 | The question asks for the policy explanation of refunds for damaged items, which is answered by the document index alone with no record lookup needed. |
| r028 | How many deliveries in July 2018 were late enough to qualify for compensation under the policy? | data+docs | data+docs | yes | 1.000 | 1206 | 607/384/77 | The question requires the late-delivery compensation policy threshold from docs plus delivery records from July 2018 counted via data. |
| r030 | For the top 5 products by units sold in November 2017, what are their return windows? | data+docs | data+docs | yes | 1.000 | 967 | 609/384/88 | Data is needed to rank products by units sold in November 2017 and identify the top 5, while docs is needed for the return window policy that applies to them. |
| r031 | Give me a count of late deliveries from 1 to 7 August 2018. | data | data | yes | 1.000 | 1197 | 606/384/82 | The question asks for a count of late deliveries within a specific date range, which requires querying order records via the data agent; no policy text is needed. |
| r032 | Delivery time question: when was order 32407a9eb4d1f77142ade9ae12f4068f actually delivered? | data | data | yes | 1.000 | 1262 | 618/384/79 | The question asks for a specific order's actual delivery date, which is a record lookup handled by the data agent; no policy text is needed. |
| r033 | In February 2018, how many deliveries were at least 3 days late, and how much compensation does each affected customer get? | data+docs | data+docs | yes | 1.000 | 1080 | 615/384/82 | The question needs a count of late deliveries from order records (data) plus the late-delivery compensation policy amount per affected customer (docs). |
| r034 | I want to start selling on the marketplace. What are the requirements? | docs | docs | yes | 1.000 | 682 | 602/384/68 | The question asks about seller onboarding requirements, which is policy content covered by the document index. |
| r037 | Can I pay with boleto and still split the amount into installments? | docs | docs | yes | 1.000 | 960 | 604/384/72 | The question asks about payment method and installment policy, which is answered by the policy documents in the docs index. |
| r040 | How many days late does a delivery have to be before the customer gets compensated? | docs | docs | yes | 1.000 | 778 | 604/384/70 | The question asks for the late-delivery compensation policy threshold, which is answered from the document index. |
| r041 | 成为卖家需要提供哪些材料？ | docs | docs | yes | 1.000 | 1117 | 596/384/74 | The question asks what materials are required to become a seller, which is answered by seller onboarding policy in the document index. |
| r042 | 谢谢你的帮助，再见！ | direct | direct | yes | 1.000 | 816 | 595/384/72 | This is a simple farewell/small talk message requiring no lookups, so only the direct agent is needed. |
| r043 | 订单 8109a41bee614af16733370276b23727 晚到了，我能拿到补偿吗？ | data+docs | data+docs | yes | 1.000 | 1274 | 614/384/85 | The question asks whether a specific order (needs its delivery record from data) qualifies for late-delivery compensation (needs the compensation policy from docs). |
| r044 | Sorry, I'm new here. Is it okay to write to you in English instead of Portuguese? | direct | direct | yes | 1.000 | 1099 | 608/384/73 | This is a small-talk question about the assistant itself (language of communication), requiring no policy or record lookups. |
| r045 | Good morning, how are you doing today? | direct | direct | yes | 1.000 | 1174 | 597/384/69 | This is a greeting and small talk about the assistant, requiring no lookups or policy evidence. |
| r046 | how many ordres got delivered in oct 2017? | data | data | yes | 1.000 | 946 | 600/384/81 | The question asks for a count of delivered orders in a specific month, which requires querying order records via the data agent; no policy text is needed. |
| r047 | 运费是怎么计算的？ | docs | docs | yes | 1.000 | 944 | 593/384/71 | The question asks how shipping fees are calculated, which is a shipping policy matter answered from the document index. |
| r049 | How many orders were placed in January 2017? | data | data | yes | 1.000 | 946 | 599/384/76 | The question asks for a count of orders in a specific date range, which requires the read-only data tools and no policy text. |
| r050 | I want to return order 7057341a79b7adc56f566fbd7c81211c on 20 August 2018. Is that still within the return window for that product category? | data+docs | data+docs | yes | 1.000 | 1075 | 633/384/87 | The question needs the order's details (delivery/purchase date and product category) from the data tools plus the return-window policy for that category from the docs. |
| r051 | How many orders were canceled between 2017-11-01 and 2017-11-30? | data | data | yes | 1.000 | 939 | 610/384/82 | The question asks for a count of canceled orders within a date range, which is a record/aggregate lookup handled by the data agent with no policy component. |
| r052 | Does the compensation policy cover order 6a7f35ca0fe2ac17fb1e6088070ab810? | data+docs | data+docs | yes | 1.000 | 1295 | 615/384/78 | The question requires the late-delivery compensation policy from docs plus the specific order's details from data to determine whether it qualifies. |
| r053 | 订单 9bba41f9513fb6efddaa9b8a851ab873 现在是什么状态？ | data | data | yes | 1.000 | 911 | 615/384/79 | The question asks for the current status of a specific order, which is a record lookup handled by the data agent; no policy text is needed. |
| r054 | ok bye, have a gud one | direct | direct | yes | 1.000 | 1003 | 596/384/68 | This is a casual sign-off, so only the direct agent is needed for small talk. |
| r055 | How long do I have to return a piece of furniture? | docs | docs | yes | 1.000 | 1016 | 600/384/71 | The question asks for the return window policy, which is answered from the document index by the docs agent. |
| r057 | What payment methods are accepted? | docs | docs | yes | 1.000 | 1106 | 594/384/69 | The question asks for policy information about accepted payment methods, which is answered from the document index. |

Errors: 0
