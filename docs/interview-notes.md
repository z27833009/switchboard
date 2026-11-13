# Interview notes

Five minute read. Measured numbers come from committed reports in `evals/reports/`; targets are named in [requirements.md](spec/requirements.md). Model rows use qwen3-8b on LM Studio 1.2.0; the gate and injection suites are deterministic.

## 1. Ports and adapters (ADR 0001)

One package: a framework-free core with nine ports, everything else an adapter chosen by YAML and entry points. The import contract keeps core to the standard library plus pydantic, and every v1 addition was an adapter or a class.

## 2. Plans over agents (ADR 0011)

The supervisor returns registered agent names plus a reason, not an enum route. Prompt and schema come from the tenant-filtered registry; plans are validated, repaired once, and a gate failure re-plans. ClarifyAgent proved one class plus one entry point.

## 3. LangGraph plus an agent loop (ADR 0002)

The plan graph uses Send fan-out and a generic merge; the control group is a framework-free loop with all read-only tools. On the routing set the plan graph scores 0.9231 and the loop 0.6410 (first decision only); tool call scores 0.9500 and 0.9250. The loop is faster (p50 1132.0 against 4466.0 ms).

## 4. Read-only tools and run tokens (ADRs 0005, 0014)

Five tools run in a separate FastMCP process over a SELECT-only role, in read-only transactions with a 5 s timeout and a 200-row cap, and every call writes an audit row. The tenant comes only from a signed HS256 run token.

## 5. Bounded context (ADR 0012)

The model sees a summary, total count, at most 20 rows, a truncated flag and a result_id, with `result_page@1` for more. Above 70 percent of the 8k window, old results become one-line placeholders; the prompt and newest result stay untouched.

## 6. Prefix caching (ADR 0006)

Prompts put the system prompt, sorted tool definitions and instructions first, with tenant facts, history, query and evidence last; a test asserts byte-identical prefixes. Providers bill the repeated prefix at the cached rate; LM Studio reports no cached tokens.

## 7. Evaluation and the CI gate (ADR 0008)

Datasets are versioned JSONL, frozen once a report cites them; routing cases were labeled blind and prompt work sees dev only. CI replays recorded output, so the gate measures the pipeline (parsing, validation, normalization, scoring), not the model; a prompt change forces a re-record. Baselines are 0.9231 and 0.9500 with a 0.05 drop limit.

## 8. Deterministic gate (ADR 0013)

Four pure checks (numbers grounded, citations valid, evidence present, merge complete) run without a model call; a failure triggers one retry or re-plan. On 30 cases `numbers_grounded` scores precision 0.9000 and recall 1.0000, the others 1.0000/1.0000, case accuracy 0.9667; the false positive is g013.

## 9. Tracing (ADR 0004)

Every boundary call makes an OTel span; PostgreSQL is the default exporter and the trace page reads it, while the MCP server continues the trace from traceparent. A flush timeout marks a trace incomplete; `tracing.exporter: otlp` swaps in Jaeger.

## 10. Tenants, retrieval and rewriting (design 11, 9.2, 9.3)

Runs, traces, feedback and audit are tenant-filtered in SQL. Retrieval fuses pgvector cosine with full text by reciprocal rank fusion, and rewriting runs only with history or a weak first score. On 40 v3 cases conditional hybrid scores recall@5 1.0000 and MRR 0.8521 against 1.0000 and 0.8250 as written.

## 11. Cost accounting (design 12.4)

`config/prices.yaml` carries a verification date and source URL per non-local row, a zero row for LM Studio, and cached input priced separately. Cost travels with every response, so a rejected attempt or a fallback stays exact; `group_by` reports unpriced runs.

## 12. Injection guardrail (design 13.1)

Tool results and chunks reach the model as data blocks, and a versioned pattern set (15 rules, six families) flags instruction-like content and records pattern ids, never the text. On 40 samples detection is 1.0000, false positives 0.1000 and precision 0.9091.

## 13. Semantic cache (ADR 0007)

The lookup runs before the supervisor, keyed by tenant, normalized query, embedding and the tenant's document index version; a hit costs one embedding and makes no model call. Only plans whose agents are all cacheable are stored, so data answers never are, and a session with history skips the lookup. On 50 v5 pairs bge-m3 cosine does not separate paraphrases from near misses well: at 0.94 the hit rate is 0.0400 and the false-hit rate 0.0400, and at 0.99 both are 0.0000. The demo therefore keeps the cache wired but disabled, and the sweep is the result.

## 14. Known limitations

- The two routing rows measure different things on one local 7B model.
- Six DeepSeek combinations are not run (no key).
- `numbers_grounded` rejects some correct derived numbers; one measured false positive (g013).
- The UI is functional; the visual pass is v2, and T2.16 was cancelled.
- The semantic cache is built, but its measured false-hit rate is above target (0.04 at the chosen 0.94, target 0.02), so the demo tenants keep it off; the report shows the sweep.
- Gate, injection and cache labels are constructed, not human.

## What to say in five minutes

1. Nine ports; core is standard library plus pydantic.
2. Routing is a validated plan over registered agents.
3. Measured: 0.9231 against 0.6410 routing; 0.9500 against 0.9250 tool call.
4. Read-only tools, an audit row, tenant from the signed token.
5. 20-row view plus paging; old results cleared above 70 percent.
6. Stable prefix for caching; versioned price table.
7. Frozen datasets; the mock gate measures the pipeline; 0.05 drop; gate 0.9667.
8. OTel with PostgreSQL default and a Jaeger switch; hybrid retrieval, recall@5 1.0000, MRR 0.8521.
9. Limits: DeepSeek pending, one derived-number false positive, v2 visual pass, T2.16 cut, cache off because its measured false-hit rate is above target.

Measured: the six reports above. Target, not measured: DeepSeek, p50 under 8 s for data+docs on a cloud model, and startup under 15 minutes. Local targets 0.75 routing and 0.60 tool call are exceeded. The cache false-hit target is not met at the chosen threshold; the measured sweep is in the cache report.
