# Eval comparison: provider by orchestrator

Merged from the JSON reports in `evals/reports` for suites `routing`, `toolcall`, `rag`, providers `lmstudio`, `deepseek` and orchestrators `langgraph`, `agent_loop`. Each row copies the numbers of its report; nothing is averaged or estimated. `not run` means no report exists for that combination, and the reason after the colon is the one supplied on the command line. Smoke reports (`--limit`) are not compared.

Measured: 12 of 12 combinations; not run: 0; smoke: 0.

| Suite | Provider | Orchestrator | Model | Status | Dataset | Cases | Primary metric | Value | Secondary metric | Value | p50 ms | p95 ms | Tokens per 100 queries | Cost per 100 queries | Report |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| routing | lmstudio | langgraph | qwen3-8b | measured | v1 | 39 | plan_accuracy | 0.9231 | plan_jaccard | 0.9359 | 4466.0 | 5551.7 | 54156.4 | 0.000000 (no API cost) | [routing-lmstudio.md](routing-lmstudio.md) |
| routing | lmstudio | agent_loop | qwen3-8b | measured | v1 | 39 | plan_accuracy | 0.6410 | plan_jaccard | 0.7564 | 1132.0 | 1870.2 | 131517.9 | 0.000000 (no API cost) | [routing-agent_loop-lmstudio.md](routing-agent_loop-lmstudio.md) |
| routing | deepseek | langgraph | deepseek-flash | measured | v1 | 39 | plan_accuracy | 0.9487 | plan_jaccard | 0.9615 | 1054.0 | 1301.5 | 68366.7 | 0.016440 | [routing-deepseek.md](routing-deepseek.md) |
| routing | deepseek | agent_loop | deepseek-flash | measured | v1 | 39 | plan_accuracy | 0.9487 | plan_jaccard | 0.9615 | 1129.0 | 1798.9 | 130941.0 | 0.018582 | [routing-agent_loop-deepseek.md](routing-agent_loop-deepseek.md) |
| toolcall | lmstudio | langgraph | qwen3-8b | measured | v1 | 40 | both_accuracy | 0.9500 | argument_accuracy | 0.9500 | 3407.5 | 3747.8 | 88115.0 | 0.000000 (no API cost) | [toolcall-lmstudio.md](toolcall-lmstudio.md) |
| toolcall | lmstudio | agent_loop | qwen3-8b | measured | v1 | 40 | both_accuracy | 0.9250 | argument_accuracy | 0.9250 | 1296.5 | 1501.4 | 121950.0 | 0.000000 (no API cost) | [toolcall-agent_loop-lmstudio.md](toolcall-agent_loop-lmstudio.md) |
| toolcall | deepseek | langgraph | deepseek-flash | measured | v1 | 40 | both_accuracy | 1.0000 | argument_accuracy | 1.0000 | 1114.5 | 1440.8 | 97817.5 | 0.018579 | [toolcall-deepseek.md](toolcall-deepseek.md) |
| toolcall | deepseek | agent_loop | deepseek-flash | measured | v1 | 40 | both_accuracy | 0.9750 | argument_accuracy | 0.9750 | 1067.5 | 1376.4 | 129830.0 | 0.016806 | [toolcall-agent_loop-deepseek.md](toolcall-agent_loop-deepseek.md) |
| rag | lmstudio | langgraph | qwen3-8b | measured | v3 | 40 | recall_at_5_conditional_hybrid | 1.0000 | mrr_conditional_hybrid | 0.8521 | 606.0 | 1218.8 | 19345.0 | 0.000000 (no API cost) | [rag-lmstudio.md](rag-lmstudio.md) |
| rag | lmstudio | agent_loop | qwen3-8b | measured | v3 | 40 | recall_at_5_conditional_hybrid | 1.0000 | mrr_conditional_hybrid | 0.8521 | 595.0 | 1157.3 | 19345.0 | 0.000000 (no API cost) | [rag-agent_loop-lmstudio.md](rag-agent_loop-lmstudio.md) |
| rag | deepseek | langgraph | deepseek-flash | measured | v3 | 40 | recall_at_5_conditional_hybrid | 1.0000 | mrr_conditional_hybrid | 0.8708 | 1191.0 | 2708.7 | 17457.5 | 0.005948 | [rag-deepseek.md](rag-deepseek.md) |
| rag | deepseek | agent_loop | deepseek-flash | measured | v3 | 40 | recall_at_5_conditional_hybrid | 1.0000 | mrr_conditional_hybrid | 0.8708 | 1246.0 | 2717.5 | 17450.0 | 0.005939 | [rag-agent_loop-deepseek.md](rag-agent_loop-deepseek.md) |

## Notes

- Plan accuracy is the exact-set routing score of the suite. Tool-call accuracy is `both_accuracy`: the first tool name and its required arguments both match. Rag is recall@5 and MRR of `conditional_hybrid`, the production retrieval path (conditional rewrite plus hybrid search); the other three strategies stay in the rag report.
- Latency is copied from each report. Routing and toolcall p50 and p95 are one model call per case, not an end-to-end run. Rag p50 and p95 are the per-case wall time of all four retrieval strategies, including the embedding call and any rewrite call.
- Tokens and cost per 100 queries scale the suite's measured totals by its case count: `total * 100 / cases`. A local provider has an explicit zero price row, so its cost is 0 and the cell says "no API cost". A report written before the price table keeps a null cost by design; when `config/prices.yaml` has a zero row for that provider, this table applies it and the JSON says `cost_source` `price_table_zero`. A null cost with no zero row is shown as "not priced" and is never turned into an invented zero.
- Routing and toolcall have one measured row per orchestrator. Rag measures retrieval, which both adapters share: its langgraph and agent_loop rows run the same strategy code, so any difference between them is run-to-run timing, not a mechanism difference.
- The toolcall agent_loop row scores the first decision of the loop with the loop's full toolset. The suite does not execute tools, and a live loop can call other tools on later steps.
- Every row links its per-suite markdown report; this comparison never rewrites those reports.
