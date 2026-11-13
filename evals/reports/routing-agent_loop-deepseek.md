# Routing eval: deepseek

## Run

- Dataset: `v1` at `evals/datasets/v1/routing.jsonl`
- Split: `test`, scored 39 of 39 cases in the split (60 in the dataset)
- Orchestrator: `agent_loop`; provider: `deepseek`; model: `deepseek-flash`
- Generated: 2026-11-13T12:50:29.256572+00:00
- Wall time: 45.4 s

## Metrics

| Metric | Value |
| --- | --- |
| plan_accuracy | 0.9487 |
| plan_jaccard | 0.9615 |
| case_pass_rate | 0.9487 |
| unambiguous_plan_accuracy | 1.0000 |
| ambiguous_plan_accuracy | 0.6000 |

Agent loop mode: each case sends the first step of the agent loop (design.md section 6.9): the production loop prompt, the default tenant's read-only tools and the platform tools `result_page@1` and `search_docs@1`. The plan is derived from the tool calls in that decision with the same function the adapter uses. The suite does not execute tools, mirroring the supervisor mode, which does not run agents. A live loop can call more tools on later steps, so a whole run's plan is a superset of the first decision's: with exact-set scoring this number can land above or below the live loop's plan accuracy. It measures the first decision; the comparison report puts it beside the supervisor mode.

## Latency

| Window | p50 ms | p95 ms | min ms | max ms | mean ms |
| --- | --- | --- | --- | --- | --- |
| Model | 1129.0 | 1798.9 | 809.0 | 2192.0 | 1161.923 |
| Runner wall | 1130.0 | 1798.9 | 810.0 | 2192.0 | 1162.026 |

## Usage

| Input tokens | Cached input tokens | Output tokens | Total tokens |
| --- | --- | --- | --- |
| 47326 | 38912 | 3741 | 51067 |

Cost: USD 0.007247 for this run, from `config/prices.yaml` (verified on 2026-11-12, https://api-docs.deepseek.com/quick_start/pricing). Cached input tokens are billed at the cached rate.

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
| r003 | Give me an order summary for September 2017 broken down by status. | data | data | yes | 1.000 | 1066 | 1210/0/80 | Derived from the tools the run called: orders_summary. |
| r008 | my packge d24b9ff725ca614bc99aa08a07f8a06c came super late, do i get anything back for that? | data | data+docs | no | 0.500 | 884 | 1229/1024/107 | Derived from the tools the run called: order_lookup, search_docs. |
| r009 | Order dfe367de1f1445f0ea5f00e2f3e04abb arrived late. Am I eligible for compensation? | data+docs | data+docs | yes | 1.000 | 1215 | 1226/1024/109 | Derived from the tools the run called: order_lookup, search_docs. |
| r010 | Can you look up orders for me, or do you only answer policy questions? | direct | direct | yes | 1.000 | 1793 | 1211/1024/170 | The run called no tool, so it answered directly. |
| r012 | 退货后多久能收到退款？ | docs | docs | yes | 1.000 | 809 | 1202/1024/82 | Derived from the tools the run called: search_docs. |
| r015 | If my order arrives late, what compensation am I entitled to? | docs | docs | yes | 1.000 | 1007 | 1208/1024/71 | Derived from the tools the run called: search_docs. |
| r016 | Was order 07460fefc54c9c13abb2bc6ca25e0d17 paid in installments, and if so, what are the installment rules? | data+docs | data+docs | yes | 1.000 | 1406 | 1233/1024/106 | Derived from the tools the run called: order_lookup, search_docs. |
| r017 | As a seller, I'd like to know which products brought in the most revenue in December 2017. | data | data | yes | 1.000 | 1129 | 1217/1024/115 | Derived from the tools the run called: top_products. |
| r018 | What kinds of questions can you help me with? | docs | direct | no | 0.000 | 2192 | 1205/1024/208 | The run called no tool, so it answered directly. |
| r019 | What fees do sellers pay to join the platform? | docs | docs | yes | 1.000 | 866 | 1205/1024/70 | Derived from the tools the run called: search_docs. |
| r020 | Show me the top 10 products by revenue between June 1 and June 30, 2018. | data | data | yes | 1.000 | 1268 | 1217/1024/101 | Derived from the tools the run called: top_products. |
| r023 | Which products sold the most units from October through December 2017? | data | data | yes | 1.000 | 1129 | 1209/1024/100 | Derived from the tools the run called: top_products. |
| r024 | What is the status of order 66c737db5f67fdd09273a03a6dc911d8? | data | data | yes | 1.000 | 841 | 1222/1024/59 | Derived from the tools the run called: order_lookup. |
| r025 | 2017年12月延迟交付的订单中，有多少符合补偿条件？ | data+docs | data+docs | yes | 1.000 | 1400 | 1211/1024/154 | Derived from the tools the run called: orders_late, search_docs. |
| r026 | Please pull order c975ae74962ebff9c04ea5ab827c90c5 and explain which payment rules apply to the method it used. | data+docs | data+docs | yes | 1.000 | 1143 | 1228/1024/127 | Derived from the tools the run called: order_lookup, search_docs. |
| r027 | Please explain how refunds work when an item arrives damaged. | docs | docs | yes | 1.000 | 1180 | 1207/1024/121 | Derived from the tools the run called: search_docs. |
| r028 | How many deliveries in July 2018 were late enough to qualify for compensation under the policy? | data+docs | data+docs | yes | 1.000 | 1289 | 1214/1024/140 | Derived from the tools the run called: orders_late, search_docs. |
| r030 | For the top 5 products by units sold in November 2017, what are their return windows? | data+docs | data+docs | yes | 1.000 | 1248 | 1216/1024/166 | Derived from the tools the run called: search_docs, top_products. |
| r031 | Give me a count of late deliveries from 1 to 7 August 2018. | data | data | yes | 1.000 | 902 | 1213/1024/88 | Derived from the tools the run called: orders_late. |
| r032 | Delivery time question: when was order 32407a9eb4d1f77142ade9ae12f4068f actually delivered? | data | data | yes | 1.000 | 1047 | 1225/1024/65 | Derived from the tools the run called: order_lookup. |
| r033 | In February 2018, how many deliveries were at least 3 days late, and how much compensation does each affected customer get? | data+docs | data+docs | yes | 1.000 | 910 | 1222/1024/140 | Derived from the tools the run called: orders_late, search_docs. |
| r034 | I want to start selling on the marketplace. What are the requirements? | docs | docs | yes | 1.000 | 1188 | 1209/1024/74 | Derived from the tools the run called: search_docs. |
| r037 | Can I pay with boleto and still split the amount into installments? | docs | docs | yes | 1.000 | 1530 | 1211/1024/126 | Derived from the tools the run called: search_docs. |
| r040 | How many days late does a delivery have to be before the customer gets compensated? | docs | docs | yes | 1.000 | 1170 | 1211/1024/58 | Derived from the tools the run called: search_docs. |
| r041 | 成为卖家需要提供哪些材料？ | docs | docs | yes | 1.000 | 1091 | 1202/1024/60 | Derived from the tools the run called: search_docs. |
| r042 | 谢谢你的帮助，再见！ | direct | direct | yes | 1.000 | 923 | 1201/1024/27 | The run called no tool, so it answered directly. |
| r043 | 订单 8109a41bee614af16733370276b23727 晚到了，我能拿到补偿吗？ | data+docs | data+docs | yes | 1.000 | 1012 | 1220/1024/100 | Derived from the tools the run called: order_lookup, search_docs. |
| r044 | Sorry, I'm new here. Is it okay to write to you in English instead of Portuguese? | direct | direct | yes | 1.000 | 1184 | 1215/1024/73 | The run called no tool, so it answered directly. |
| r045 | Good morning, how are you doing today? | direct | direct | yes | 1.000 | 887 | 1204/1024/64 | The run called no tool, so it answered directly. |
| r046 | how many ordres got delivered in oct 2017? | data | data | yes | 1.000 | 1006 | 1207/1024/85 | Derived from the tools the run called: orders_summary. |
| r047 | 运费是怎么计算的？ | docs | docs | yes | 1.000 | 1376 | 1199/1024/125 | Derived from the tools the run called: search_docs. |
| r049 | How many orders were placed in January 2017? | data | data | yes | 1.000 | 894 | 1206/1024/68 | Derived from the tools the run called: orders_summary. |
| r050 | I want to return order 7057341a79b7adc56f566fbd7c81211c on 20 August 2018. Is that still within the return window for that product category? | data+docs | data+docs | yes | 1.000 | 1194 | 1240/1024/121 | Derived from the tools the run called: order_lookup, search_docs. |
| r051 | How many orders were canceled between 2017-11-01 and 2017-11-30? | data | data | yes | 1.000 | 1852 | 1217/1024/85 | Derived from the tools the run called: orders_summary. |
| r052 | Does the compensation policy cover order 6a7f35ca0fe2ac17fb1e6088070ab810? | data+docs | data+docs | yes | 1.000 | 1220 | 1222/1024/120 | Derived from the tools the run called: order_lookup, search_docs. |
| r053 | 订单 9bba41f9513fb6efddaa9b8a851ab873 现在是什么状态？ | data | data | yes | 1.000 | 1154 | 1221/1024/59 | Derived from the tools the run called: order_lookup. |
| r054 | ok bye, have a gud one | direct | direct | yes | 1.000 | 882 | 1203/1024/10 | The run called no tool, so it answered directly. |
| r055 | How long do I have to return a piece of furniture? | docs | docs | yes | 1.000 | 952 | 1207/1024/55 | Derived from the tools the run called: search_docs. |
| r057 | What payment methods are accepted? | docs | docs | yes | 1.000 | 1076 | 1201/1024/52 | Derived from the tools the run called: search_docs. |

Errors: 0
