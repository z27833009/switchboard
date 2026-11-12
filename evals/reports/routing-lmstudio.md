# Routing eval: lmstudio

## Run

- Dataset: `v1` at `evals/datasets/v1/routing.jsonl`
- Split: `test`, scored 39 of 39 cases in the split (60 in the dataset)
- Orchestrator: `langgraph`; provider: `lmstudio`; model: `qwen3-8b`
- Generated: 2026-11-12T19:58:17.037491+00:00
- Wall time: 24.122 s

## Metrics

| Metric | Value |
| --- | --- |
| plan_accuracy | 0.5385 |
| plan_jaccard | 0.6667 |
| case_pass_rate | 0.5385 |
| unambiguous_plan_accuracy | 0.5294 |
| ambiguous_plan_accuracy | 0.6000 |

## Latency

| Window | p50 ms | p95 ms | min ms | max ms | mean ms |
| --- | --- | --- | --- | --- | --- |
| Model | 2353.0 | 2991.0 | 1804.0 | 3409.0 | 2431.974 |
| Runner wall | 2354.0 | 2991.0 | 1804.0 | 3409.0 | 2432.128 |

## Usage

| Input tokens | Cached input tokens | Output tokens | Total tokens |
| --- | --- | --- | --- |
| 14626 | 0 | 1773 | 16399 |

Cost is not reported as USD: no price table exists until T2.06, so the JSON value is null. A local model such as LM Studio has no API cost.

## Plan confusion matrix

| Expected \ Actual | data | direct | docs | none |
| --- | --- | --- | --- | --- |
| data | 12 | 0 | 0 | 0 |
| data+docs | 10 | 0 | 0 | 0 |
| direct | 1 | 4 | 0 | 0 |
| docs | 6 | 0 | 5 | 1 |

## Cases (39)

| Case | Query | Expected | Actual | Exact | Jaccard | Latency ms | Tokens in/cached/out | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| r003 | Give me an order summary for September 2017 broken down by status. | data | data | yes | 1.000 | 2811 | 370/0/43 | The data agent can answer questions about orders, including summaries broken down by status. |
| r008 | my packge d24b9ff725ca614bc99aa08a07f8a06c came super late, do i get anything back for that? | data | data | yes | 1.000 | 2952 | 396/0/46 | The question is about a package delay and potential compensation, which falls under order and delivery inquiries. |
| r009 | Order dfe367de1f1445f0ea5f00e2f3e04abb arrived late. Am I eligible for compensation? | data+docs | data | no | 0.500 | 2945 | 390/0/46 | The question is about order delivery and potential compensation, which falls under the data agent's responsibility. |
| r010 | Can you look up orders for me, or do you only answer policy questions? | direct | data | no | 0.000 | 3342 | 369/0/52 | The 'data' agent can handle order-related lookups, while the question also implies a need to clarify policy-related capabilities. |
| r012 | 退货后多久能收到退款？ | docs | docs | yes | 1.000 | 2259 | 361/0/43 | The question is about shop policies, which can be answered using the document index. |
| r015 | If my order arrives late, what compensation am I entitled to? | docs | data | no | 0.000 | 2216 | 366/0/42 | The data agent can answer questions about orders, delivery, and compensation policies. |
| r016 | Was order 07460fefc54c9c13abb2bc6ca25e0d17 paid in installments, and if so, what are the installment rules? | data+docs | data | no | 0.500 | 2433 | 397/0/46 | The question is about order details and payment methods, which can be answered using the data agent. |
| r017 | As a seller, I'd like to know which products brought in the most revenue in December 2017. | data | data | yes | 1.000 | 2208 | 377/0/43 | The data agent can answer questions about order and product performance, including revenue analysis. |
| r018 | What kinds of questions can you help me with? | docs | none | no | 0.000 | 2844 | 363/0/56 | plan validation failed: exclusive agents must run alone: direct |
| r019 | What fees do sellers pay to join the platform? | docs | docs | yes | 1.000 | 2283 | 363/0/45 | The question about seller fees relates to shop policies, which are answered by the docs agent. |
| r020 | Show me the top 10 products by revenue between June 1 and June 30, 2018. | data | data | yes | 1.000 | 2623 | 379/0/52 | The data agent can answer questions about order, delivery, product, and seller information, including revenue data for specific time periods. |
| r023 | Which products sold the most units from October through December 2017? | data | data | yes | 1.000 | 2199 | 369/0/44 | The question is about product sales data, which can be answered using the data agent. |
| r024 | What is the status of order 66c737db5f67fdd09273a03a6dc911d8? | data | data | yes | 1.000 | 2383 | 390/0/47 | The question is about the status of a specific order, which can be answered using the data agent. |
| r025 | 2017年12月延迟交付的订单中，有多少符合补偿条件？ | data+docs | data | no | 0.500 | 2324 | 373/0/46 | The question is about order delivery and compensation conditions, which falls under the data agent's responsibility. |
| r026 | Please pull order c975ae74962ebff9c04ea5ab827c90c5 and explain which payment rules apply to the method it used. | data+docs | data | no | 0.500 | 2217 | 395/0/44 | The question involves order details and payment rules, which are handled by the data agent. |
| r027 | Please explain how refunds work when an item arrives damaged. | docs | docs | yes | 1.000 | 2108 | 364/0/42 | The question about refund policies for damaged items requires information from the document index. |
| r028 | How many deliveries in July 2018 were late enough to qualify for compensation under the policy? | data+docs | data | no | 0.500 | 2693 | 374/0/49 | The question involves analyzing delivery data and applying a policy, which falls under the 'data' agent's responsibility. |
| r030 | For the top 5 products by units sold in November 2017, what are their return windows? | data+docs | data | no | 0.500 | 3409 | 376/0/58 | The data agent can access order and product information to retrieve the top 5 products by units sold in November 2017 and their return windows. |
| r031 | Give me a count of late deliveries from 1 to 7 August 2018. | data | data | yes | 1.000 | 2404 | 373/0/44 | The data agent can access order and delivery information to provide the count of late deliveries. |
| r032 | Delivery time question: when was order 32407a9eb4d1f77142ade9ae12f4068f actually delivered? | data | data | yes | 1.000 | 2353 | 392/0/43 | The data agent can answer questions about order delivery times using the Olist tools. |
| r033 | In February 2018, how many deliveries were at least 3 days late, and how much compensation does each affected customer get? | data+docs | data | no | 0.500 | 2471 | 382/0/44 | The question involves order and delivery data, which can be answered using the data agent. |
| r034 | I want to start selling on the marketplace. What are the requirements? | docs | docs | yes | 1.000 | 2382 | 367/0/43 | The question about starting to sell on the marketplace requires information from the document index. |
| r037 | Can I pay with boleto and still split the amount into installments? | docs | data | no | 0.000 | 2639 | 369/0/46 | The question relates to payment methods and installment options, which can be answered using the data agent. |
| r040 | How many days late does a delivery have to be before the customer gets compensated? | docs | data | no | 0.000 | 2275 | 369/0/43 | The question is about delivery policies, which falls under the data agent's responsibility. |
| r041 | 成为卖家需要提供哪些材料？ | docs | data | no | 0.000 | 2605 | 361/0/49 | The question about becoming a seller requires information on the required materials, which can be answered using the data agent. |
| r042 | 谢谢你的帮助，再见！ | direct | direct | yes | 1.000 | 2099 | 362/0/40 | The user is ending the conversation, so direct handle is sufficient. |
| r043 | 订单 8109a41bee614af16733370276b23727 晚到了，我能拿到补偿吗？ | data+docs | data | no | 0.500 | 2296 | 395/0/45 | The question is about an order and compensation, which falls under the data agent's responsibility. |
| r044 | Sorry, I'm new here. Is it okay to write to you in English instead of Portuguese? | direct | direct | yes | 1.000 | 2446 | 373/0/45 | The question is about small talk and language preference, which is handled by the direct agent. |
| r045 | Good morning, how are you doing today? | direct | direct | yes | 1.000 | 2313 | 362/0/42 | The question is about small talk and can be answered by the direct agent. |
| r046 | how many ordres got delivered in oct 2017? | data | data | yes | 1.000 | 2211 | 367/0/40 | The data agent can answer questions about orders, including delivery statistics. |
| r047 | 运费是怎么计算的？ | docs | data | no | 0.000 | 2638 | 360/0/49 | The question about shipping costs is related to order and delivery information, which can be answered by the data agent. |
| r049 | How many orders were placed in January 2017? | data | data | yes | 1.000 | 2443 | 366/0/47 | The data agent can answer questions about orders, including the number of orders placed in a specific month. |
| r050 | I want to return order 7057341a79b7adc56f566fbd7c81211c on 20 August 2018. Is that still within the return window for that product category? | data+docs | data | no | 0.500 | 2685 | 411/0/52 | The question involves checking the return window for a specific order, which requires accessing order and product information from the Olist tools. |
| r051 | How many orders were canceled between 2017-11-01 and 2017-11-30? | data | data | yes | 1.000 | 2227 | 383/0/43 | The data agent can answer questions about orders, including canceled orders between specific dates. |
| r052 | Does the compensation policy cover order 6a7f35ca0fe2ac17fb1e6088070ab810? | data+docs | data | no | 0.500 | 2107 | 388/0/41 | The data agent can access order-specific information, including compensation policy details. |
| r053 | 订单 9bba41f9513fb6efddaa9b8a851ab873 现在是什么状态？ | data | data | yes | 1.000 | 2232 | 389/0/44 | The question is about the status of a specific order, which requires accessing order data. |
| r054 | ok bye, have a gud one | direct | direct | yes | 1.000 | 2033 | 361/0/45 | The question is a closing message and should be handled by the direct agent for small talk. |
| r055 | How long do I have to return a piece of furniture? | docs | docs | yes | 1.000 | 1935 | 365/0/43 | The question is about shop policies, which can be answered using the document index. |
| r057 | What payment methods are accepted? | docs | data | no | 0.000 | 1804 | 359/0/41 | The data agent can provide information about payment methods accepted by the platform. |

Errors: 1 (r018)
