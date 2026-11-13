# Toolcall eval: deepseek

## Run

- Dataset: `v1` at `evals/datasets/v1/toolcall.jsonl`
- Split: `all`, scored 40 of 40 cases in the split (40 in the dataset)
- Orchestrator: `agent_loop`; provider: `deepseek`; model: `deepseek-flash`
- Generated: 2026-11-13T12:52:04.055449+00:00
- Wall time: 43.563 s

## Metrics

| Metric | Value |
| --- | --- |
| tool_name_accuracy | 0.9750 |
| argument_accuracy | 0.9750 |
| both_accuracy | 0.9750 |
| tool_calls_per_case | 0.9750 |
| redundant_call_rate | 0.0000 |
| cases_with_redundant_calls | 0.0000 |
| case_pass_rate | 0.9750 |

Agent loop mode: each case sends the first step of the agent loop (design.md section 6.9), the production loop prompt and the tools the adapter exposes (the default tenant's read-only tools plus `result_page@1` and `search_docs@1`), and scores the first tool call. The suite does not execute the tool or continue the loop, so a live loop can choose a different or additional tool on a later step.

## Latency

| Window | p50 ms | p95 ms | min ms | max ms | mean ms |
| --- | --- | --- | --- | --- | --- |
| Model | 1067.5 | 1376.4 | 754.0 | 1840.0 | 1086.925 |
| Runner wall | 1067.5 | 1376.4 | 754.0 | 1840.0 | 1087.075 |

## Usage

| Input tokens | Cached input tokens | Output tokens | Total tokens |
| --- | --- | --- | --- |
| 48393 | 40960 | 3539 | 51932 |

Cost: USD 0.006722 for this run, from `config/prices.yaml` (verified on 2026-11-12, https://api-docs.deepseek.com/quick_start/pricing). Cached input tokens are billed at the cached rate.

## Cases (40)

| Case | Query | Expected tool | Actual calls | Name | Args | Calls | Latency ms | Tokens in/cached/out | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| t001 | Count the invoiced orders in April 2017. | orders_summary@1 {"date_from": "2017-04-01", "date_to": "2017-04-30", "status": "invoiced"} | orders_summary@1({"date_from": "2017-04-01", "date_to": "2017-04-30", "status": "invoiced"}) | yes | yes | 1 | 1296 | 1206/1024/86 |  |
| t002 | How many orders were shipped between 2018-07-10 and 2018-07-19? | orders_summary@1 {"date_from": "2018-07-10", "date_to": "2018-07-19", "status": "shipped"} | orders_summary@1({"date_from": "2018-07-10", "date_to": "2018-07-19", "status": "shipped"}) | yes | yes | 1 | 932 | 1217/1024/85 |  |
| t003 | 2018年上半年有多少已送达的订单？ | orders_summary@1 {"date_from": "2018-01-01", "date_to": "2018-06-30", "status": "delivered"} | orders_summary@1({"date_from": "2018-01-01", "date_to": "2018-06-30", "status": "delivered"}) | yes | yes | 1 | 1038 | 1204/1024/101 |  |
| t004 | How many orders were canceled in 2017? | orders_summary@1 {"date_from": "2017-01-01", "date_to": "2017-12-31", "status": "canceled"} | orders_summary@1({"date_from": "2017-01-01", "date_to": "2017-12-31", "status": "canceled"}) | yes | yes | 1 | 821 | 1205/1024/85 |  |
| t005 | Give me an order summary in Q4 2017. | orders_summary@1 {"date_from": "2017-10-01", "date_to": "2017-12-31"} | orders_summary@1({"date_from": "2017-10-01", "date_to": "2017-12-31"}) | yes | yes | 1 | 1066 | 1207/1024/79 |  |
| t006 | 统计一下2017年9月的订单情况 | orders_summary@1 {"date_from": "2017-09-01", "date_to": "2017-09-30"} | orders_summary@1({"date_from": "2017-09-01", "date_to": "2017-09-30"}) | yes | yes | 1 | 1056 | 1204/1024/78 |  |
| t007 | What did order volume look like on 2017-04-04? | orders_summary@1 {"date_from": "2017-04-04", "date_to": "2017-04-04"} | orders_summary@1({"date_from": "2017-04-04", "date_to": "2017-04-04"}) | yes | yes | 1 | 1085 | 1210/1024/78 |  |
| t008 | How many orders were placed between 2017-04-22 and 2017-05-05? | orders_summary@1 {"date_from": "2017-04-22", "date_to": "2017-05-05"} | orders_summary@1({"date_from": "2017-04-22", "date_to": "2017-05-05"}) | yes | yes | 1 | 785 | 1217/1024/68 |  |
| t009 | 2017年7月13日一共有多少订单？ | orders_summary@1 {"date_from": "2017-07-13", "date_to": "2017-07-13"} | orders_summary@1({"date_from": "2017-07-13", "date_to": "2017-07-13"}) | yes | yes | 1 | 1149 | 1206/1024/68 |  |
| t010 | Give me an order summary in July 2018. | orders_summary@1 {"date_from": "2018-07-01", "date_to": "2018-07-31"} | orders_summary@1({"date_from": "2018-07-01", "date_to": "2018-07-31"}) | yes | yes | 1 | 967 | 1206/1024/80 |  |
| t011 | How many orders were at least 7 days late in July 2017? | orders_late@1 {"date_from": "2017-07-01", "date_to": "2017-07-31", "min_days_late": 7} | orders_late@1({"date_from": "2017-07-01", "date_to": "2017-07-31", "min_days_late": 7}) | yes | yes | 1 | 1069 | 1211/1024/88 |  |
| t012 | 2018年上半年延迟至少10天的订单有多少？ | orders_late@1 {"date_from": "2018-01-01", "date_to": "2018-06-30", "min_days_late": 10} | orders_late@1({"date_from": "2018-01-01", "date_to": "2018-06-30", "min_days_late": 10}) | yes | yes | 1 | 781 | 1205/1024/102 |  |
| t013 | List deliveries that were 3 or more days late in 2017. | orders_late@1 {"date_from": "2017-01-01", "date_to": "2017-12-31", "min_days_late": 3} | orders_late@1({"date_from": "2017-01-01", "date_to": "2017-12-31", "min_days_late": 3}) | yes | yes | 1 | 1241 | 1210/1024/88 |  |
| t014 | How many orders were at least 14 days late in Q2 2017? | orders_late@1 {"date_from": "2017-04-01", "date_to": "2017-06-30", "min_days_late": 14} | orders_late@1({"date_from": "2017-04-01", "date_to": "2017-06-30", "min_days_late": 14}) | yes | yes | 1 | 904 | 1212/1024/88 |  |
| t015 | 2017年第二季度有多少订单延迟送达？ | orders_late@1 {"date_from": "2017-04-01", "date_to": "2017-06-30"} | orders_late@1({"date_from": "2017-04-01", "date_to": "2017-06-30", "min_days_late": 1}) | yes | yes | 1 | 1177 | 1205/1024/100 |  |
| t016 | Which orders were delivered after the estimated date between 2017-01-04 and 2017-01-10? | orders_late@1 {"date_from": "2017-01-04", "date_to": "2017-01-10"} | orders_late@1({"date_from": "2017-01-04", "date_to": "2017-01-10", "min_days_late": 1}) | yes | yes | 1 | 843 | 1220/1024/99 |  |
| t017 | How many orders arrived late in 2017? | orders_late@1 {"date_from": "2017-01-01", "date_to": "2017-12-31"} | orders_late@1({"date_from": "2017-01-01", "date_to": "2017-12-31", "min_days_late": 1}) | yes | yes | 1 | 883 | 1205/1024/98 |  |
| t018 | 查一下2018年上半年的延迟订单 | orders_late@1 {"date_from": "2018-01-01", "date_to": "2018-06-30"} | orders_late@1({"date_from": "2018-01-01", "date_to": "2018-06-30", "min_days_late": 1}) | yes | yes | 1 | 1278 | 1204/1024/102 |  |
| t019 | Show me late deliveries in August 2018. | orders_late@1 {"date_from": "2018-08-01", "date_to": "2018-08-31"} | orders_late@1({"date_from": "2018-08-01", "date_to": "2018-08-31", "min_days_late": 1}) | yes | yes | 1 | 940 | 1205/1024/99 |  |
| t020 | Show me late deliveries in January 2018. | orders_late@1 {"date_from": "2018-01-01", "date_to": "2018-01-31"} | orders_late@1({"date_from": "2018-01-01", "date_to": "2018-01-31", "min_days_late": 1}) | yes | yes | 1 | 1195 | 1205/1024/99 |  |
| t021 | 2017年10月15日销售额最高的前3个商品是哪些？ | top_products@1 {"date_from": "2017-10-15", "date_to": "2017-10-15", "metric": "revenue", "limit": 3} | top_products@1({"date_from": "2017-10-15", "date_to": "2017-10-15", "metric": "revenue", "limit": 3}) | yes | yes | 1 | 954 | 1211/1024/101 |  |
| t022 | What were the 5 best-selling products by units sold in 2017? | top_products@1 {"date_from": "2017-01-01", "date_to": "2017-12-31", "metric": "count", "limit": 5} | top_products@1({"date_from": "2017-01-01", "date_to": "2017-12-31", "metric": "count", "limit": 5}) | yes | yes | 1 | 1821 | 1211/1024/100 |  |
| t023 | Top 3 products by revenue in February 2017? | top_products@1 {"date_from": "2017-02-01", "date_to": "2017-02-28", "metric": "revenue", "limit": 3} | top_products@1({"date_from": "2017-02-01", "date_to": "2017-02-28", "metric": "revenue", "limit": 3}) | yes | yes | 1 | 1156 | 1207/1024/101 |  |
| t024 | 2018年上半年销量前10的商品有哪些？ | top_products@1 {"date_from": "2018-01-01", "date_to": "2018-06-30", "metric": "count", "limit": 10} | top_products@1({"date_from": "2018-01-01", "date_to": "2018-06-30", "metric": "count", "limit": 10}) | yes | yes | 1 | 1144 | 1204/1024/118 |  |
| t025 | Top 10 products by number of items sold on 2017-11-13 | top_products@1 {"date_from": "2017-11-13", "date_to": "2017-11-13", "metric": "count", "limit": 10} | top_products@1({"date_from": "2017-11-13", "date_to": "2017-11-13", "metric": "count", "limit": 10}) | yes | yes | 1 | 754 | 1212/1024/100 |  |
| t026 | Which 3 products made the most money in July 2017? | top_products@1 {"date_from": "2017-07-01", "date_to": "2017-07-31", "metric": "revenue", "limit": 3} | top_products@1({"date_from": "2017-07-01", "date_to": "2017-07-31", "metric": "revenue", "limit": 3}) | yes | yes | 1 | 1353 | 1209/1024/101 |  |
| t027 | 2018-05-23到2018-06-01之间销售额最高的前3个商品是哪些？ | top_products@1 {"date_from": "2018-05-23", "date_to": "2018-06-01", "metric": "revenue", "limit": 3} | top_products@1({"date_from": "2018-05-23", "date_to": "2018-06-01", "metric": "revenue", "limit": 3}) | yes | yes | 1 | 1122 | 1218/1024/114 |  |
| t028 | Top 3 products by number of items sold in Q2 2017 | top_products@1 {"date_from": "2017-04-01", "date_to": "2017-06-30", "metric": "count", "limit": 3} | top_products@1({"date_from": "2017-04-01", "date_to": "2017-06-30", "metric": "count", "limit": 3}) | yes | yes | 1 | 1221 | 1210/1024/116 |  |
| t029 | Which 3 products made the most money in 2017? | top_products@1 {"date_from": "2017-01-01", "date_to": "2017-12-31", "metric": "revenue", "limit": 3} | top_products@1({"date_from": "2017-01-01", "date_to": "2017-12-31", "metric": "revenue", "limit": 3}) | yes | yes | 1 | 970 | 1208/1024/101 |  |
| t030 | 2018年上半年销量前5的商品有哪些？ | top_products@1 {"date_from": "2018-01-01", "date_to": "2018-06-30", "metric": "count", "limit": 5} | top_products@1({"date_from": "2018-01-01", "date_to": "2018-06-30", "metric": "count", "limit": 5}) | yes | yes | 1 | 890 | 1204/1024/118 |  |
| t031 | Where is my order bdbcb0e21f0d85538d17911900b26610? | order_lookup@1 {"order_id": "bdbcb0e21f0d85538d17911900b26610"} | order_lookup@1({"order_id": "bdbcb0e21f0d85538d17911900b26610"}) | yes | yes | 1 | 1031 | 1218/1024/66 |  |
| t032 | What is the status of order 6f7c4d0e0bfea3787bfa435c00423420? | order_lookup@1 {"order_id": "6f7c4d0e0bfea3787bfa435c00423420"} | order_lookup@1({"order_id": "6f7c4d0e0bfea3787bfa435c00423420"}) | yes | yes | 1 | 1109 | 1223/1024/60 |  |
| t033 | 帮我查一下订单86bf271e1a69a73159db69b6b820d8e2的情况 | order_lookup@1 {"order_id": "86bf271e1a69a73159db69b6b820d8e2"} | order_lookup@1({"order_id": "86bf271e1a69a73159db69b6b820d8e2"}) | yes | yes | 1 | 943 | 1220/1024/68 |  |
| t034 | Show me the details of order 75de3533485debf7b6eb8014b9c9ab76. | order_lookup@1 {"order_id": "75de3533485debf7b6eb8014b9c9ab76"} | order_lookup@1({"order_id": "75de3533485debf7b6eb8014b9c9ab76"}) | yes | yes | 1 | 1353 | 1222/1024/59 |  |
| t035 | What is the status of order 6c8409ca697ac1cdf422adf792fbf759? | order_lookup@1 {"order_id": "6c8409ca697ac1cdf422adf792fbf759"} | order_lookup@1({"order_id": "6c8409ca697ac1cdf422adf792fbf759"}) | yes | yes | 1 | 1002 | 1220/1024/57 |  |
| t036 | 帮我查一下订单b9499283a775ccfa9da92e6e75e9b4b9的情况 | order_lookup@1 {"order_id": "b9499283a775ccfa9da92e6e75e9b4b9"} | order_lookup@1({"order_id": "b9499283a775ccfa9da92e6e75e9b4b9"}) | yes | yes | 1 | 1070 | 1221/1024/69 |  |
| t037 | What data can you query? | describe_schema@1 {} | describe_schema@1({}) | yes | yes | 1 | 1142 | 1201/1024/35 |  |
| t038 | Which tables and fields are available in the order database? | describe_schema@1 {} | describe_schema@1({}) | yes | yes | 1 | 1089 | 1206/1024/33 |  |
| t039 | 你能查询哪些数据？ | describe_schema@1 {} |  | no | no | 0 | 1840 | 1200/1024/220 |  |
| t040 | Describe the database schema you have access to. | describe_schema@1 {} | describe_schema@1({}) | yes | yes | 1 | 1007 | 1204/1024/31 |  |

Errors: 0
