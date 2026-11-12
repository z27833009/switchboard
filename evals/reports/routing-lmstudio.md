# Routing eval: lmstudio

## Run

- Dataset: `v1` at `evals/datasets/v1/routing.jsonl`
- Split: `test`, scored 39 of 39 cases in the split (60 in the dataset)
- Orchestrator: `langgraph`; provider: `lmstudio`; model: `qwen3-8b`
- Generated: 2026-11-12T20:47:26.540777+00:00
- Wall time: 45.499 s

## Metrics

| Metric | Value |
| --- | --- |
| plan_accuracy | 0.9231 |
| plan_jaccard | 0.9359 |
| case_pass_rate | 0.9231 |
| unambiguous_plan_accuracy | 0.9706 |
| ambiguous_plan_accuracy | 0.6000 |

## Latency

| Window | p50 ms | p95 ms | min ms | max ms | mean ms |
| --- | --- | --- | --- | --- | --- |
| Model | 4466.0 | 5551.7 | 1695.0 | 5725.0 | 4515.923 |
| Runner wall | 4466.0 | 5551.8 | 1695.0 | 5725.0 | 4516.205 |

## Usage

| Input tokens | Cached input tokens | Output tokens | Total tokens |
| --- | --- | --- | --- |
| 19228 | 0 | 1893 | 21121 |

Cost is not reported as USD: no price table exists until T2.06, so the JSON value is null. A local model such as LM Studio has no API cost.

## Plan confusion matrix

| Expected \ Actual | data | data+docs | direct | docs | none |
| --- | --- | --- | --- | --- | --- |
| data | 11 | 1 | 0 | 0 | 0 |
| data+docs | 0 | 10 | 0 | 0 | 0 |
| direct | 0 | 0 | 4 | 0 | 1 |
| docs | 0 | 0 | 1 | 11 | 0 |

## Cases (39)

| Case | Query | Expected | Actual | Exact | Jaccard | Latency ms | Tokens in/cached/out | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| r003 | Give me an order summary for September 2017 broken down by status. | data | data | yes | 1.000 | 3993 | 488/0/45 | The question requires data about specific orders and their statuses for September 2017. |
| r008 | my packge d24b9ff725ca614bc99aa08a07f8a06c came super late, do i get anything back for that? | data | data+docs | no | 0.500 | 1695 | 514/0/55 | The question involves policy (late-delivery compensation) and requires data to determine if the package was late based on delivery dates. |
| r009 | Order dfe367de1f1445f0ea5f00e2f3e04abb arrived late. Am I eligible for compensation? | data+docs | data+docs | yes | 1.000 | 5193 | 508/0/55 | The question requires policy text on late-delivery compensation (docs) and data to determine eligibility based on order details (data). |
| r010 | Can you look up orders for me, or do you only answer policy questions? | direct | none | no | 0.000 | 2932 | 487/0/55 | plan validation failed: exclusive agents must run alone: direct |
| r012 | 退货后多久能收到退款？ | docs | docs | yes | 1.000 | 4583 | 479/0/47 | The question asks about the policy related to returns and refunds, which is covered by the docs agent. |
| r015 | If my order arrives late, what compensation am I entitled to? | docs | docs | yes | 1.000 | 4489 | 484/0/51 | The question asks about compensation for late delivery, which is covered in the policy text provided by the 'docs' agent. |
| r016 | Was order 07460fefc54c9c13abb2bc6ca25e0d17 paid in installments, and if so, what are the installment rules? | data+docs | data+docs | yes | 1.000 | 4717 | 515/0/56 | The question requires checking the payment method of a specific order (data) and retrieving the relevant installment rules from policy documents (docs). |
| r017 | As a seller, I'd like to know which products brought in the most revenue in December 2017. | data | data | yes | 1.000 | 4529 | 495/0/45 | The question requires data on product revenue from specific orders in December 2017. |
| r018 | What kinds of questions can you help me with? | docs | direct | no | 0.000 | 4428 | 481/0/44 | The question is about the assistant's capabilities, which is handled by the direct agent. |
| r019 | What fees do sellers pay to join the platform? | docs | docs | yes | 1.000 | 4337 | 481/0/47 | The question asks about fees related to joining the platform, which is covered under seller onboarding policies. |
| r020 | Show me the top 10 products by revenue between June 1 and June 30, 2018. | data | data | yes | 1.000 | 4135 | 497/0/47 | The question requires data on product revenue and rankings, which is provided by the 'data' agent. |
| r023 | Which products sold the most units from October through December 2017? | data | data | yes | 1.000 | 4402 | 487/0/57 | The question asks for specific sales data on product units sold during a particular period, which requires the 'data' agent to retrieve and aggregate order records. |
| r024 | What is the status of order 66c737db5f67fdd09273a03a6dc911d8? | data | data | yes | 1.000 | 4466 | 508/0/47 | The question asks for the status of a specific order, which requires data from the read-only tools. |
| r025 | 2017年12月延迟交付的订单中，有多少符合补偿条件？ | data+docs | data+docs | yes | 1.000 | 5585 | 491/0/54 | The question requires data from specific orders (to count eligible orders) and policy text from docs (to determine compensation conditions). |
| r026 | Please pull order c975ae74962ebff9c04ea5ab827c90c5 and explain which payment rules apply to the method it used. | data+docs | data+docs | yes | 1.000 | 5725 | 513/0/51 | The question requires accessing specific order data (data agent) and retrieving relevant payment policy text (docs agent). |
| r027 | Please explain how refunds work when an item arrives damaged. | docs | docs | yes | 1.000 | 5458 | 482/0/43 | The question requires policy text about refunds, which is provided by the docs agent. |
| r028 | How many deliveries in July 2018 were late enough to qualify for compensation under the policy? | data+docs | data+docs | yes | 1.000 | 5548 | 492/0/45 | The question requires data about specific deliveries and the applicable policy text for compensation. |
| r030 | For the top 5 products by units sold in November 2017, what are their return windows? | data+docs | data+docs | yes | 1.000 | 4444 | 494/0/44 | The question requires data on product sales and policy information on return windows. |
| r031 | Give me a count of late deliveries from 1 to 7 August 2018. | data | data | yes | 1.000 | 4238 | 491/0/43 | The question asks for a count of late deliveries, which is a data record. |
| r032 | Delivery time question: when was order 32407a9eb4d1f77142ade9ae12f4068f actually delivered? | data | data | yes | 1.000 | 4262 | 510/0/47 | The question asks for a specific order's delivery date, which requires data from the read-only tools. |
| r033 | In February 2018, how many deliveries were at least 3 days late, and how much compensation does each affected customer get? | data+docs | data+docs | yes | 1.000 | 4344 | 500/0/49 | The question requires data about the number of late deliveries and policy information on compensation for affected customers. |
| r034 | I want to start selling on the marketplace. What are the requirements? | docs | docs | yes | 1.000 | 4325 | 485/0/44 | The question asks about seller onboarding requirements, which is covered by the docs agent. |
| r037 | Can I pay with boleto and still split the amount into installments? | docs | docs | yes | 1.000 | 4337 | 487/0/44 | The question involves payment methods and installments, which are covered in the policy text. |
| r040 | How many days late does a delivery have to be before the customer gets compensated? | docs | docs | yes | 1.000 | 4209 | 487/0/38 | The question requires policy text about late-delivery compensation. |
| r041 | 成为卖家需要提供哪些材料？ | docs | docs | yes | 1.000 | 4203 | 479/0/48 | The question asks about the requirements to become a seller, which is covered by the 'docs' agent. |
| r042 | 谢谢你的帮助，再见！ | direct | direct | yes | 1.000 | 4316 | 480/0/46 | The question is a closing statement, so the 'direct' agent is needed for small talk. |
| r043 | 订单 8109a41bee614af16733370276b23727 晚到了，我能拿到补偿吗？ | data+docs | data+docs | yes | 1.000 | 4583 | 513/0/54 | The question involves checking the policy for late delivery compensation (docs) and verifying the order details to determine eligibility (data). |
| r044 | Sorry, I'm new here. Is it okay to write to you in English instead of Portuguese? | direct | direct | yes | 1.000 | 4848 | 491/0/48 | The question is about the assistant's capabilities and language preference, which falls under the 'direct' agent. |
| r045 | Good morning, how are you doing today? | direct | direct | yes | 1.000 | 4512 | 480/0/39 | The question is about small talk and the assistant's capabilities. |
| r046 | how many ordres got delivered in oct 2017? | data | data | yes | 1.000 | 4587 | 485/0/53 | The question asks for the number of orders delivered in October 2017, which requires data from the read-only tools. |
| r047 | 运费是怎么计算的？ | docs | docs | yes | 1.000 | 4432 | 478/0/47 | The question is about shipping policy, which requires the 'docs' agent to provide relevant policy text. |
| r049 | How many orders were placed in January 2017? | data | data | yes | 1.000 | 4423 | 484/0/53 | The question asks for the number of orders placed in January 2017, which requires data from the read-only tools. |
| r050 | I want to return order 7057341a79b7adc56f566fbd7c81211c on 20 August 2018. Is that still within the return window for that product category? | data+docs | data+docs | yes | 1.000 | 4910 | 529/0/57 | The question requires checking the return policy (docs) and specific order details (data) to determine if the return is within the window. |
| r051 | How many orders were canceled between 2017-11-01 and 2017-11-30? | data | data | yes | 1.000 | 4811 | 501/0/51 | The question asks for a count of canceled orders within a specific date range, which requires data from the read-only tools. |
| r052 | Does the compensation policy cover order 6a7f35ca0fe2ac17fb1e6088070ab810? | data+docs | data+docs | yes | 1.000 | 5423 | 506/0/76 | The question requires checking the compensation policy (docs) and verifying details about order 6a7f35ca0fe2ac17fb1e6088070ab810 (data). |
| r053 | 订单 9bba41f9513fb6efddaa9b8a851ab873 现在是什么状态？ | data | data | yes | 1.000 | 5314 | 507/0/47 | The question asks for the status of a specific order, which requires data from the read-only tools. |
| r054 | ok bye, have a gud one | direct | direct | yes | 1.000 | 4929 | 479/0/44 | The question is a closing message and requires the 'direct' agent for small talk. |
| r055 | How long do I have to return a piece of furniture? | docs | docs | yes | 1.000 | 4587 | 483/0/35 | The question requires policy text about returns. |
| r057 | What payment methods are accepted? | docs | docs | yes | 1.000 | 3869 | 477/0/42 | The question asks about payment methods, which is covered in the policy text. |

Errors: 1 (r010)
