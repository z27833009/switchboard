# Critical Review of the Plan

Version 0.2, 2026-11-12. Written before any code exists, to be revisited at the end of each phase.

## 0. Changes in 0.2

A design review on 2026-11-12 compared the 0.1 plan with current agent products (Codex, Claude Code, Claude, ChatGPT, Perplexity, DeepSeek) and with its own goals. Changes:

| # | Problem in 0.1 | Change in 0.2 | Where |
| --- | --- | --- | --- |
| 1 | `Route` was an enum in core; adding an agent changed core, the prompt, the graph and the escalation path, which contradicted G2. `parallel` could only mean sql plus rag | The supervisor returns a plan (a set of registered agents). Agent specs drive the prompt, the schema, the cache rule and the graph. Escalation became re-planning with the gate's reason | ADR 0011, design 4, 6 |
| 2 | Cache lookup ran after the supervisor, so a hit still paid for a model call (contradicting AC-10d); the key contained a nondeterministic route; the graph wiring was inconsistent with the text | Lookup before the supervisor, key without route, plan label stored on the entry | ADR 0007, design 10 |
| 3 | The tool server checked an allowlist against a tenant id supplied by the caller, so the "server-side enforcement" was not a trust boundary | Signed short-lived run token minted by the API; the server takes the tenant only from the token | ADR 0014, design 7.3 |
| 4 | It was not stated what the CI regression gate on a mock provider measures, nor what happens on a fixture miss | Fixture key defined, misses fail loudly, README states the gate measures the pipeline | ADR 0008, design 18 |
| 5 | Routing confidence was self-reported by the model, uncalibrated and unused | Removed | design 4 |
| 6 | LangGraph parallel writes, trace read-after-run race, and a foreign key that the MCP process could violate | Reducers on state; run row at start, `force_flush`, no foreign key on `spans.run_id` | design 6.2, 12.2 |
| 7 | `SqlAgent` and `RagAgent` named techniques, not capabilities; the SQL agent writes no SQL | `DataAgent`, `DocsAgent` | design 6.4 |
| 8 | The routing design was never compared with the default design of current agent products: one model with all tools in a loop | `AgentLoopOrchestrator` in v1 as a control group, measured on the same suites; replaces the v2 `SequentialOrchestrator`, which proved only that the port exists | ADR 0002, design 6.9 |
| 9 | The quality gate was an uncalibrated model judging a model | Deterministic checks first; Reflexion-style retry with concrete feedback or a re-plan; LLM judge only as a calibrated second layer in v2 | ADR 0013, design 6.8 |
| 10 | Up to 200 tool rows could enter an 8k context; no compaction strategy | Bounded tool result views with paging, layered context policy, no model summarization | ADR 0012, design 8.6 |
| 11 | Provider prefix caching, the largest and cheapest cost lever, was not considered | Stable prompt prefix layout, cached tokens in usage, spans and prices | ADR 0006, design 8.5 |
| 12 | One-shot retrieval with the raw query | Conditional query rewriting in v1, measured against three alternatives | design 9.3 |
| 13 | Numbers in answers were not linked to their source | Data citations from answer numbers to tool spans and audit rows | design 6.4 |
| 14 | Streaming was a v2 item although every product has it | Step events in Phase 1 from the same spans as the trace, token streaming in v1 | design 12.5 |
| 15 | Follow-up questions failed; no path from user feedback to datasets | Minimal sessions and a `clarify` agent in v1; feedback harvest into candidate cases | design 20.1, tasks T2.09, T2.10 |
| 16 | Permission vocabulary `require_approval` | `allow`, `ask`, implicit deny, as in agent products | design 7.3 |
| 17 | No explanation of why there is no sandbox | Stated explicitly | design 7.6 |
| 18 | v1 had grown by about 13 hours | Visual design pass moved to v2; semantic cache moved to the end of v1 as the first cut | tasks |

## 1. Risks

| # | Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- | --- |
| R1 | Small local models (7B class in LM Studio) plan and call tools poorly, so Phase 1 numbers look bad | high | medium | Report honestly; frame as the baseline that the DeepSeek comparison in v1 improves on. Choose the local model deliberately (one with documented tool-call support) and record the model name in every report |
| R2 | Ports are abstracted wrong on the first try and the v1 adapters (second provider, second orchestrator, semantic cache, hybrid retriever) force interface changes | medium | medium | Phase 1 implements one adapter per port and keeps signatures minimal; expect one round of port cleanup at the start of v1 and budget 3 h for it |
| R3 | LangGraph API churn breaks the orchestrator adapter, in particular `Send` and reducers | medium | low | Pin versions; the port means the blast radius is one package; the agent loop adapter is a second working orchestrator from v1 |
| R4 | Phase 1 overruns; it is now estimated at 45 hours | high | high for motivation | Cut list below; T1.17 (first measured report) is the real finish line, not the UI |
| R5 | Olist dataset download needs a Kaggle account and manual steps, hurting "one command" | high | low | Loader accepts a local directory; README documents the five minute download; a 1000-row sample fixture ships in the repo for tests and CI |
| R6 | Synthetic policy documents feel fake to an interviewer | medium | low | Keep them short, realistic and clearly labeled; the point is the pipeline, not the corpus |
| R7 | Eval datasets written by the same person who writes the prompts are biased toward passing | medium | medium | Datasets are finished before the prompts. Routing questions come from a separate session that saw only a brief; the author labels blind; disagreements become the `ambiguous` tag. Toolcall labels are computed by code. Prompt work sees only the dev split; reports score the test split and show the confusion matrix, not just accuracy |
| R8 | Windows plus Docker Desktop plus LM Studio networking issues (`host.docker.internal`) | medium | medium | Verify in T1.02 before anything else depends on it; document the fallback of running the API outside Docker with `uv run` |
| R9 | AI assistant token budget for writing the code is limited | medium | medium | Specs are detailed on purpose so implementation sessions are short; each task has a clear verification so sessions end cleanly |
| R10 | Trace volume in PostgreSQL grows during eval runs, now including full tool results as span events | medium | low | Results capped at 64 KB per span; eval runs tag spans with `eval_run_id`; a cleanup command deletes them |
| R11 | Small local models ignore citation markers, so data citations and the citation check fail often | high | medium | Citation parsing tolerates missing markers (answer is still returned, marked uncited); the rate of uncited answers is reported per model, which is itself a finding |
| R12 | `numbers_grounded` rejects correct derived numbers (percentages, differences) | medium | medium | Measured per check on the gate suite; if precision is low, derived numbers get an allowance (computed from evidence numbers) rather than the check being removed |
| R13 | The agent loop beats the plan graph on every metric | medium | low | Then the README says so. The project's claim is that the choice is measured, not that routing wins |
| R14 | SSE through the web container's proxy is buffered and the live steps arrive all at once | medium | low | Disable proxy buffering for the stream route; integration test reads events before the run ends |

## 2. Where this plan may be over-engineered

- **Entry-point registry.** A dict mapping names to classes would do for a solo project. Entry points are kept because "a third-party package can add an adapter or an agent" is a concrete sentence for an interview and costs about an hour. If it causes friction, fall back to a dict in `registry.py` with the same public API.
- **Tool versioning with dual-serving majors.** Nobody will depend on `orders_late@1`. The rule is kept in the manifest and the eval dataset pins, but the "serve both majors for a phase" behavior is not implemented until a major bump actually happens.
- **Two database roles and read-only transactions.** Slightly heavier than a code review, but it is the one security claim that a test can prove in ten lines, so it stays.
- **Run tokens for a single tool server on a private network.** One could argue the network is the boundary. Kept because without it the "server-side enforcement" sentence is false, and the cost is about 30 lines plus four tests.
- **Pass-through gate and cache nodes in Phase 1.** Adds two empty nodes. Kept because it makes the v1 diff tiny and the graph diagram stable across phases.
- **Human-in-the-loop hook that never fires in Phase 1 or v1.** Not implemented until v2; only the risk field and the `ask` list exist earlier. That is the right size.
- **OTel instead of a simple event log.** OTel adds some setup, but it now feeds three consumers (trace page, SSE steps, Jaeger in v1). Kept.
- **Multi-tenancy from day one.** Carrying `tenant_id` everywhere is cheap; the permission model and seller filter are v1. The cost is one extra field, the benefit is never retrofitting it.
- **Four retrieval strategies in the rag report.** Each is a flag on the same retriever, and the comparison is what makes the rewriting decision defensible.

## 3. Cut order if Phase 1 runs out of time

Cut from the top down; each line names what the demo loses.

1. T1.14 web UI: replace with a tiny static page that prints SSE steps and the JSON trace. Loses visual tree and citation chips, keeps every claim.
2. `result_page@1`: keep the bounded view and the `truncated` flag, drop paging. Loses the ability to read past 20 rows, which the demo questions do not need.
3. T1.16 regression gate: keep lint and tests in CI, add the gate in v1. Loses the "regression gate in CI" bullet temporarily.
4. Structured output strategy 2 in T1.05: ship native JSON schema plus prompted JSON only. Loses nothing visible.
5. `top_products@1` and `describe_schema@1` tools: three tools still demonstrate the layer.

Never cut: T1.07 read-only tools with run tokens and audit, data citations in T1.10, T1.12 tracing, T1.15 and T1.17 measured reports. Without those the project is a demo, not evidence. The multi-agent plan (`data+docs`) is no longer on the cut list: with the generic graph it costs little and it is the best demo moment.

## 4. What I would challenge in my own design

- **Is upfront routing still the right design?** Current agent products mostly use one model with all tools in a loop. Routing can win on small models (shorter prompts, fewer tools per call), on cost and on predictability; the loop can win on questions that need a second look. v1 measures both on the same suites instead of arguing. Whatever the result, it is a stronger interview answer than a design that was never compared.
- **Is LangGraph pulling its weight behind a port?** For one generic graph, a hand-written async function would be shorter. LangGraph is kept because it is what the target jobs ask for, `Send` and reducers make the dynamic fan-out explicit, and interrupts are useful in v2. The agent loop adapter is framework-free, so the project shows both.
- **Is a semantic cache worth a whole task?** On a demo corpus with no real traffic, the hit rate is whatever the dataset makes it. The value is in the placement (before routing), the false-hit measurement and the invalidation design. Provider prefix caching delivers real savings with no infrastructure, which is why it is in Phase 1 and the semantic cache is the first v1 cut.
- **Can deterministic checks be wrong?** Yes: a correct derived number fails `numbers_grounded`. That is why every check has its own precision and recall and why the gate acts at most once per run.
- **Will the local model make the toolcall suite meaningless?** Possibly. The suite should be run on DeepSeek early in v1 to see the ceiling; if even a cloud model is below 0.85, the tool descriptions or the dataset need work, and that finding is itself a good README paragraph.

## 5. Open questions for the author

1. Does the Olist seller id mapping make sense as the tenant boundary, or should tenants map to product categories? Seller is proposed because it is the most natural "merchant" story.
2. Should eval reports be committed to the repository or published as CI artifacts only? The plan commits selected reports so the README can link to them.
3. Is 45 hours for Phase 1 acceptable, or should the first cut (unstyled UI to static page) be taken up front?

Resolved in 0.2: the visual design pass happens in v2 (T3.03), after the comparison work. The Phase 1 local models are Qwen3-8B and bge-m3, pending the VRAM check in T1.02 (design 8.1).
