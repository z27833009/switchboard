# Routing eval: lmstudio

## Run

- Dataset: `v1` at `evals/datasets/v1/routing.jsonl`
- Split: `test`, scored 39 of 39 cases in the split (60 in the dataset)
- Orchestrator: `agent_loop`; provider: `lmstudio`; model: `qwen3-8b`
- Generated: 2026-11-13T00:31:07.927755+00:00
- Wall time: 143.678 s

## Metrics

| Metric | Value |
| --- | --- |
| plan_accuracy | 0.6410 |
| plan_jaccard | 0.7564 |
| case_pass_rate | 0.6410 |
| unambiguous_plan_accuracy | 0.6765 |
| ambiguous_plan_accuracy | 0.4000 |

Agent loop mode: each case sends the first step of the agent loop (design.md section 6.9): the production loop prompt, the default tenant's read-only tools and the platform tools `result_page@1` and `search_docs@1`. The plan is derived from the tool calls in that decision with the same function the adapter uses. The suite does not execute tools, mirroring the supervisor mode, which does not run agents. A live loop can call more tools on later steps, so a whole run's plan is a superset of the first decision's: with exact-set scoring this number can land above or below the live loop's plan accuracy. It measures the first decision and is not a substitute for the mechanism comparison in T2.12.

## Latency

| Window | p50 ms | p95 ms | min ms | max ms | mean ms |
| --- | --- | --- | --- | --- | --- |
| Model | 1132.0 | 1870.2 | 312.0 | 103469.0 | 3681.385 |
| Runner wall | 1132.0 | 1870.2 | 312.0 | 103469.0 | 3681.564 |

## Usage

| Input tokens | Cached input tokens | Output tokens | Total tokens |
| --- | --- | --- | --- |
| 45465 | 0 | 5827 | 51292 |

Cost: USD 0.000000 for this run. The provider is local (LM Studio), so the run has no API cost; the price table records zero for it on purpose.

## Plan confusion matrix

| Expected \ Actual | data | data+docs | direct | docs |
| --- | --- | --- | --- | --- |
| data | 11 | 0 | 0 | 1 |
| data+docs | 7 | 1 | 0 | 2 |
| direct | 0 | 0 | 5 | 0 |
| docs | 0 | 0 | 4 | 8 |

## Cases (39)

| Case | Query | Expected | Actual | Exact | Jaccard | Latency ms | Tokens in/cached/out | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| r003 | Give me an order summary for September 2017 broken down by status. | data | data | yes | 1.000 | 1446 | 1161/0/48 | Derived from the tools the run called: orders_summary. |
| r008 | my packge d24b9ff725ca614bc99aa08a07f8a06c came super late, do i get anything back for that? | data | docs | no | 0.000 | 795 | 1187/0/31 | Derived from the tools the run called: search_docs. |
| r009 | Order dfe367de1f1445f0ea5f00e2f3e04abb arrived late. Am I eligible for compensation? | data+docs | docs | no | 0.500 | 817 | 1181/0/31 | Derived from the tools the run called: search_docs. |
| r010 | Can you look up orders for me, or do you only answer policy questions? | direct | direct | yes | 1.000 | 625 | 1160/0/24 | The run called no tool, so it answered directly. |
| r012 | 退货后多久能收到退款？ | docs | direct | no | 0.000 | 897 | 1151/0/39 | The run called no tool, so it answered directly. |
| r015 | If my order arrives late, what compensation am I entitled to? | docs | docs | yes | 1.000 | 711 | 1157/0/31 | Derived from the tools the run called: search_docs. |
| r016 | Was order 07460fefc54c9c13abb2bc6ca25e0d17 paid in installments, and if so, what are the installment rules? | data+docs | data | no | 0.500 | 1100 | 1188/0/48 | Derived from the tools the run called: order_lookup. |
| r017 | As a seller, I'd like to know which products brought in the most revenue in December 2017. | data | data | yes | 1.000 | 1381 | 1168/0/62 | Derived from the tools the run called: top_products. |
| r018 | What kinds of questions can you help me with? | docs | direct | no | 0.000 | 2988 | 1154/0/132 | The run called no tool, so it answered directly. |
| r019 | What fees do sellers pay to join the platform? | docs | docs | yes | 1.000 | 725 | 1154/0/31 | Derived from the tools the run called: search_docs. |
| r020 | Show me the top 10 products by revenue between June 1 and June 30, 2018. | data | data | yes | 1.000 | 1376 | 1170/0/62 | Derived from the tools the run called: top_products. |
| r023 | Which products sold the most units from October through December 2017? | data | data | yes | 1.000 | 1421 | 1160/0/61 | Derived from the tools the run called: top_products. |
| r024 | What is the status of order 66c737db5f67fdd09273a03a6dc911d8? | data | data | yes | 1.000 | 1157 | 1181/0/51 | Derived from the tools the run called: order_lookup. |
| r025 | 2017年12月延迟交付的订单中，有多少符合补偿条件？ | data+docs | data | no | 0.500 | 1333 | 1163/0/58 | Derived from the tools the run called: orders_late. |
| r026 | Please pull order c975ae74962ebff9c04ea5ab827c90c5 and explain which payment rules apply to the method it used. | data+docs | data | no | 0.500 | 1134 | 1186/0/49 | Derived from the tools the run called: order_lookup. |
| r027 | Please explain how refunds work when an item arrives damaged. | docs | docs | yes | 1.000 | 103469 | 1155/0/4096 | Derived from the tools the run called: search_docs. |
| r028 | How many deliveries in July 2018 were late enough to qualify for compensation under the policy? | data+docs | data | no | 0.500 | 1305 | 1165/0/58 | Derived from the tools the run called: orders_late. |
| r030 | For the top 5 products by units sold in November 2017, what are their return windows? | data+docs | data | no | 0.500 | 1344 | 1167/0/60 | Derived from the tools the run called: top_products. |
| r031 | Give me a count of late deliveries from 1 to 7 August 2018. | data | data | yes | 1.000 | 1285 | 1164/0/58 | Derived from the tools the run called: orders_late. |
| r032 | Delivery time question: when was order 32407a9eb4d1f77142ade9ae12f4068f actually delivered? | data | data | yes | 1.000 | 1132 | 1183/0/50 | Derived from the tools the run called: order_lookup. |
| r033 | In February 2018, how many deliveries were at least 3 days late, and how much compensation does each affected customer get? | data+docs | data | no | 0.500 | 1298 | 1173/0/58 | Derived from the tools the run called: orders_late. |
| r034 | I want to start selling on the marketplace. What are the requirements? | docs | docs | yes | 1.000 | 699 | 1158/0/30 | Derived from the tools the run called: search_docs. |
| r037 | Can I pay with boleto and still split the amount into installments? | docs | direct | no | 0.000 | 1132 | 1160/0/50 | The run called no tool, so it answered directly. |
| r040 | How many days late does a delivery have to be before the customer gets compensated? | docs | docs | yes | 1.000 | 719 | 1160/0/31 | Derived from the tools the run called: search_docs. |
| r041 | 成为卖家需要提供哪些材料？ | docs | docs | yes | 1.000 | 710 | 1151/0/31 | Derived from the tools the run called: search_docs. |
| r042 | 谢谢你的帮助，再见！ | direct | direct | yes | 1.000 | 535 | 1150/0/23 | The run called no tool, so it answered directly. |
| r043 | 订单 8109a41bee614af16733370276b23727 晚到了，我能拿到补偿吗？ | data+docs | data | no | 0.500 | 1306 | 1185/0/58 | Derived from the tools the run called: orders_late. |
| r044 | Sorry, I'm new here. Is it okay to write to you in English instead of Portuguese? | direct | direct | yes | 1.000 | 625 | 1164/0/27 | The run called no tool, so it answered directly. |
| r045 | Good morning, how are you doing today? | direct | direct | yes | 1.000 | 623 | 1153/0/26 | The run called no tool, so it answered directly. |
| r046 | how many ordres got delivered in oct 2017? | data | data | yes | 1.000 | 1228 | 1158/0/55 | Derived from the tools the run called: orders_summary. |
| r047 | 运费是怎么计算的？ | docs | docs | yes | 1.000 | 649 | 1149/0/28 | Derived from the tools the run called: search_docs. |
| r049 | How many orders were placed in January 2017? | data | data | yes | 1.000 | 1207 | 1157/0/54 | Derived from the tools the run called: orders_summary. |
| r050 | I want to return order 7057341a79b7adc56f566fbd7c81211c on 20 August 2018. Is that still within the return window for that product category? | data+docs | docs | no | 0.500 | 759 | 1202/0/32 | Derived from the tools the run called: search_docs. |
| r051 | How many orders were canceled between 2017-11-01 and 2017-11-30? | data | data | yes | 1.000 | 1227 | 1174/0/55 | Derived from the tools the run called: orders_summary. |
| r052 | Does the compensation policy cover order 6a7f35ca0fe2ac17fb1e6088070ab810? | data+docs | data+docs | yes | 1.000 | 1746 | 1179/0/79 | Derived from the tools the run called: order_lookup, search_docs. |
| r053 | 订单 9bba41f9513fb6efddaa9b8a851ab873 现在是什么状态？ | data | data | yes | 1.000 | 1091 | 1179/0/48 | Derived from the tools the run called: order_lookup. |
| r054 | ok bye, have a gud one | direct | direct | yes | 1.000 | 312 | 1152/0/11 | The run called no tool, so it answered directly. |
| r055 | How long do I have to return a piece of furniture? | docs | docs | yes | 1.000 | 791 | 1156/0/31 | Derived from the tools the run called: search_docs. |
| r057 | What payment methods are accepted? | docs | direct | no | 0.000 | 476 | 1150/0/20 | The run called no tool, so it answered directly. |

Errors: 0
