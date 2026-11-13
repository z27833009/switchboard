# Toolcall eval: deepseek

## Run

- Dataset: `v1` at `evals/datasets/v1/toolcall.jsonl`
- Split: `all`, scored 40 of 40 cases in the split (40 in the dataset)
- Orchestrator: `langgraph`; provider: `deepseek`; model: `deepseek-flash`
- Generated: 2026-11-13T12:51:16.796049+00:00
- Wall time: 44.21 s

## Metrics

| Metric | Value |
| --- | --- |
| tool_name_accuracy | 1.0000 |
| argument_accuracy | 1.0000 |
| both_accuracy | 1.0000 |
| tool_calls_per_case | 1.0000 |
| redundant_call_rate | 0.0000 |
| cases_with_redundant_calls | 0.0000 |
| case_pass_rate | 1.0000 |

## Latency

| Window | p50 ms | p95 ms | min ms | max ms | mean ms |
| --- | --- | --- | --- | --- | --- |
| Model | 1114.5 | 1440.85 | 678.0 | 1531.0 | 1103.225 |
| Runner wall | 1114.5 | 1440.85 | 679.0 | 1531.0 | 1103.35 |

## Usage

| Input tokens | Cached input tokens | Output tokens | Total tokens |
| --- | --- | --- | --- |
| 35633 | 25344 | 3494 | 39127 |

Cost: USD 0.007432 for this run, from `config/prices.yaml` (verified on 2026-11-12, https://api-docs.deepseek.com/quick_start/pricing). Cached input tokens are billed at the cached rate.

## Cases (40)

| Case | Query | Expected tool | Actual calls | Name | Args | Calls | Latency ms | Tokens in/cached/out | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| t001 | Count the invoiced orders in April 2017. | orders_summary@1 {"date_from": "2017-04-01", "date_to": "2017-04-30", "status": "invoiced"} | orders_summary@1({"date_from": "2017-04-01", "date_to": "2017-04-30", "status": "invoiced"}) | yes | yes | 1 | 997 | 887/0/86 |  |
| t002 | How many orders were shipped between 2018-07-10 and 2018-07-19? | orders_summary@1 {"date_from": "2018-07-10", "date_to": "2018-07-19", "status": "shipped"} | orders_summary@1({"date_from": "2018-07-10", "date_to": "2018-07-19", "status": "shipped"}) | yes | yes | 1 | 1233 | 898/640/85 |  |
| t003 | 2018年上半年有多少已送达的订单？ | orders_summary@1 {"date_from": "2018-01-01", "date_to": "2018-06-30", "status": "delivered"} | orders_summary@1({"date_from": "2018-01-01", "date_to": "2018-06-30", "status": "delivered"}) | yes | yes | 1 | 1021 | 885/640/99 |  |
| t004 | How many orders were canceled in 2017? | orders_summary@1 {"date_from": "2017-01-01", "date_to": "2017-12-31", "status": "canceled"} | orders_summary@1({"date_from": "2017-01-01", "date_to": "2017-12-31", "status": "canceled"}) | yes | yes | 1 | 1187 | 886/640/85 |  |
| t005 | Give me an order summary in Q4 2017. | orders_summary@1 {"date_from": "2017-10-01", "date_to": "2017-12-31"} | orders_summary@1({"date_from": "2017-10-01", "date_to": "2017-12-31"}) | yes | yes | 1 | 1476 | 888/640/79 |  |
| t006 | 统计一下2017年9月的订单情况 | orders_summary@1 {"date_from": "2017-09-01", "date_to": "2017-09-30"} | orders_summary@1({"date_from": "2017-09-01", "date_to": "2017-09-30"}) | yes | yes | 1 | 1165 | 885/640/78 |  |
| t007 | What did order volume look like on 2017-04-04? | orders_summary@1 {"date_from": "2017-04-04", "date_to": "2017-04-04"} | orders_summary@1({"date_from": "2017-04-04", "date_to": "2017-04-04"}) | yes | yes | 1 | 1414 | 891/640/78 |  |
| t008 | How many orders were placed between 2017-04-22 and 2017-05-05? | orders_summary@1 {"date_from": "2017-04-22", "date_to": "2017-05-05"} | orders_summary@1({"date_from": "2017-04-22", "date_to": "2017-05-05"}) | yes | yes | 1 | 827 | 898/640/68 |  |
| t009 | 2017年7月13日一共有多少订单？ | orders_summary@1 {"date_from": "2017-07-13", "date_to": "2017-07-13"} | orders_summary@1({"date_from": "2017-07-13", "date_to": "2017-07-13"}) | yes | yes | 1 | 1228 | 887/640/78 |  |
| t010 | Give me an order summary in July 2018. | orders_summary@1 {"date_from": "2018-07-01", "date_to": "2018-07-31"} | orders_summary@1({"date_from": "2018-07-01", "date_to": "2018-07-31"}) | yes | yes | 1 | 1113 | 887/640/78 |  |
| t011 | How many orders were at least 7 days late in July 2017? | orders_late@1 {"date_from": "2017-07-01", "date_to": "2017-07-31", "min_days_late": 7} | orders_late@1({"date_from": "2017-07-01", "date_to": "2017-07-31", "min_days_late": 7}) | yes | yes | 1 | 1274 | 892/640/88 |  |
| t012 | 2018年上半年延迟至少10天的订单有多少？ | orders_late@1 {"date_from": "2018-01-01", "date_to": "2018-06-30", "min_days_late": 10} | orders_late@1({"date_from": "2018-01-01", "date_to": "2018-06-30", "min_days_late": 10}) | yes | yes | 1 | 894 | 886/640/101 |  |
| t013 | List deliveries that were 3 or more days late in 2017. | orders_late@1 {"date_from": "2017-01-01", "date_to": "2017-12-31", "min_days_late": 3} | orders_late@1({"date_from": "2017-01-01", "date_to": "2017-12-31", "min_days_late": 3}) | yes | yes | 1 | 987 | 891/640/106 |  |
| t014 | How many orders were at least 14 days late in Q2 2017? | orders_late@1 {"date_from": "2017-04-01", "date_to": "2017-06-30", "min_days_late": 14} | orders_late@1({"date_from": "2017-04-01", "date_to": "2017-06-30", "min_days_late": 14}) | yes | yes | 1 | 1236 | 893/640/112 |  |
| t015 | 2017年第二季度有多少订单延迟送达？ | orders_late@1 {"date_from": "2017-04-01", "date_to": "2017-06-30"} | orders_late@1({"date_from": "2017-04-01", "date_to": "2017-06-30", "min_days_late": 1}) | yes | yes | 1 | 1439 | 886/640/111 |  |
| t016 | Which orders were delivered after the estimated date between 2017-01-04 and 2017-01-10? | orders_late@1 {"date_from": "2017-01-04", "date_to": "2017-01-10"} | orders_late@1({"date_from": "2017-01-04", "date_to": "2017-01-10", "min_days_late": 1}) | yes | yes | 1 | 851 | 901/640/99 |  |
| t017 | How many orders arrived late in 2017? | orders_late@1 {"date_from": "2017-01-01", "date_to": "2017-12-31"} | orders_late@1({"date_from": "2017-01-01", "date_to": "2017-12-31", "min_days_late": 1}) | yes | yes | 1 | 1036 | 886/640/98 |  |
| t018 | 查一下2018年上半年的延迟订单 | orders_late@1 {"date_from": "2018-01-01", "date_to": "2018-06-30"} | orders_late@1({"date_from": "2018-01-01", "date_to": "2018-06-30", "min_days_late": 1}) | yes | yes | 1 | 928 | 885/640/102 |  |
| t019 | Show me late deliveries in August 2018. | orders_late@1 {"date_from": "2018-08-01", "date_to": "2018-08-31"} | orders_late@1({"date_from": "2018-08-01", "date_to": "2018-08-31", "min_days_late": 1}) | yes | yes | 1 | 1241 | 886/640/99 |  |
| t020 | Show me late deliveries in January 2018. | orders_late@1 {"date_from": "2018-01-01", "date_to": "2018-01-31"} | orders_late@1({"date_from": "2018-01-01", "date_to": "2018-01-31", "min_days_late": 1}) | yes | yes | 1 | 1261 | 886/640/99 |  |
| t021 | 2017年10月15日销售额最高的前3个商品是哪些？ | top_products@1 {"date_from": "2017-10-15", "date_to": "2017-10-15", "metric": "revenue", "limit": 3} | top_products@1({"date_from": "2017-10-15", "date_to": "2017-10-15", "metric": "revenue", "limit": 3}) | yes | yes | 1 | 1394 | 892/640/113 |  |
| t022 | What were the 5 best-selling products by units sold in 2017? | top_products@1 {"date_from": "2017-01-01", "date_to": "2017-12-31", "metric": "count", "limit": 5} | top_products@1({"date_from": "2017-01-01", "date_to": "2017-12-31", "metric": "count", "limit": 5}) | yes | yes | 1 | 952 | 892/640/114 |  |
| t023 | Top 3 products by revenue in February 2017? | top_products@1 {"date_from": "2017-02-01", "date_to": "2017-02-28", "metric": "revenue", "limit": 3} | top_products@1({"date_from": "2017-02-01", "date_to": "2017-02-28", "metric": "revenue", "limit": 3}) | yes | yes | 1 | 1302 | 888/640/106 |  |
| t024 | 2018年上半年销量前10的商品有哪些？ | top_products@1 {"date_from": "2018-01-01", "date_to": "2018-06-30", "metric": "count", "limit": 10} | top_products@1({"date_from": "2018-01-01", "date_to": "2018-06-30", "metric": "count", "limit": 10}) | yes | yes | 1 | 1116 | 885/640/118 |  |
| t025 | Top 10 products by number of items sold on 2017-11-13 | top_products@1 {"date_from": "2017-11-13", "date_to": "2017-11-13", "metric": "count", "limit": 10} | top_products@1({"date_from": "2017-11-13", "date_to": "2017-11-13", "metric": "count", "limit": 10}) | yes | yes | 1 | 1136 | 893/640/113 |  |
| t026 | Which 3 products made the most money in July 2017? | top_products@1 {"date_from": "2017-07-01", "date_to": "2017-07-31", "metric": "revenue", "limit": 3} | top_products@1({"date_from": "2017-07-01", "date_to": "2017-07-31", "metric": "revenue", "limit": 3}) | yes | yes | 1 | 1531 | 890/640/115 |  |
| t027 | 2018-05-23到2018-06-01之间销售额最高的前3个商品是哪些？ | top_products@1 {"date_from": "2018-05-23", "date_to": "2018-06-01", "metric": "revenue", "limit": 3} | top_products@1({"date_from": "2018-05-23", "date_to": "2018-06-01", "metric": "revenue", "limit": 3}) | yes | yes | 1 | 919 | 899/640/114 |  |
| t028 | Top 3 products by number of items sold in Q2 2017 | top_products@1 {"date_from": "2017-04-01", "date_to": "2017-06-30", "metric": "count", "limit": 3} | top_products@1({"date_from": "2017-04-01", "date_to": "2017-06-30", "metric": "count", "limit": 3}) | yes | yes | 1 | 1070 | 891/640/128 |  |
| t029 | Which 3 products made the most money in 2017? | top_products@1 {"date_from": "2017-01-01", "date_to": "2017-12-31", "metric": "revenue", "limit": 3} | top_products@1({"date_from": "2017-01-01", "date_to": "2017-12-31", "metric": "revenue", "limit": 3}) | yes | yes | 1 | 1163 | 889/640/114 |  |
| t030 | 2018年上半年销量前5的商品有哪些？ | top_products@1 {"date_from": "2018-01-01", "date_to": "2018-06-30", "metric": "count", "limit": 5} | top_products@1({"date_from": "2018-01-01", "date_to": "2018-06-30", "metric": "count", "limit": 5}) | yes | yes | 1 | 1139 | 885/640/118 |  |
| t031 | Where is my order bdbcb0e21f0d85538d17911900b26610? | order_lookup@1 {"order_id": "bdbcb0e21f0d85538d17911900b26610"} | order_lookup@1({"order_id": "bdbcb0e21f0d85538d17911900b26610"}) | yes | yes | 1 | 766 | 899/640/66 |  |
| t032 | What is the status of order 6f7c4d0e0bfea3787bfa435c00423420? | order_lookup@1 {"order_id": "6f7c4d0e0bfea3787bfa435c00423420"} | order_lookup@1({"order_id": "6f7c4d0e0bfea3787bfa435c00423420"}) | yes | yes | 1 | 1318 | 904/768/66 |  |
| t033 | 帮我查一下订单86bf271e1a69a73159db69b6b820d8e2的情况 | order_lookup@1 {"order_id": "86bf271e1a69a73159db69b6b820d8e2"} | order_lookup@1({"order_id": "86bf271e1a69a73159db69b6b820d8e2"}) | yes | yes | 1 | 1197 | 901/640/60 |  |
| t034 | Show me the details of order 75de3533485debf7b6eb8014b9c9ab76. | order_lookup@1 {"order_id": "75de3533485debf7b6eb8014b9c9ab76"} | order_lookup@1({"order_id": "75de3533485debf7b6eb8014b9c9ab76"}) | yes | yes | 1 | 1036 | 903/768/59 |  |
| t035 | What is the status of order 6c8409ca697ac1cdf422adf792fbf759? | order_lookup@1 {"order_id": "6c8409ca697ac1cdf422adf792fbf759"} | order_lookup@1({"order_id": "6c8409ca697ac1cdf422adf792fbf759"}) | yes | yes | 1 | 932 | 901/640/57 |  |
| t036 | 帮我查一下订单b9499283a775ccfa9da92e6e75e9b4b9的情况 | order_lookup@1 {"order_id": "b9499283a775ccfa9da92e6e75e9b4b9"} | order_lookup@1({"order_id": "b9499283a775ccfa9da92e6e75e9b4b9"}) | yes | yes | 1 | 1099 | 902/768/69 |  |
| t037 | What data can you query? | describe_schema@1 {} | describe_schema@1({}) | yes | yes | 1 | 1099 | 882/640/35 |  |
| t038 | Which tables and fields are available in the order database? | describe_schema@1 {} | describe_schema@1({}) | yes | yes | 1 | 706 | 887/640/33 |  |
| t039 | 你能查询哪些数据？ | describe_schema@1 {} | describe_schema@1({}) | yes | yes | 1 | 768 | 881/640/36 |  |
| t040 | Describe the database schema you have access to. | describe_schema@1 {} | describe_schema@1({}) | yes | yes | 1 | 678 | 885/640/31 |  |

Errors: 0
